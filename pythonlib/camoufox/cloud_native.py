from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from abc import abstractmethod
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    runtime_checkable,
)

logger = logging.getLogger("camoufox.cloud_native")
SNAPSHOT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
MAX_REQUEST_BODY_BYTES = 1_048_576


def _sanitize_snapshot_key(value: Any) -> str:
    key = str(value or "").strip()
    if not SNAPSHOT_KEY_RE.fullmatch(key):
        raise ValueError(
            "snapshot_key must be 1-128 chars and contain only letters, digits, '.', '_' or '-'"
        )
    return key


def _coerce_optional_tuple(value: Optional[Sequence[Any]]) -> Optional[tuple]:
    if value is None:
        return None
    return tuple(value)


@dataclass
class SessionRequest:
    os: Optional[str] = None
    config: Dict[str, Any] = field(default_factory=dict)
    window: Optional[List[int]] = None
    fonts: List[str] = field(default_factory=list)
    custom_fonts_only: bool = False
    block_webgl: bool = False
    webgl_config: Optional[List[str]] = None
    proxy: Optional[Dict[str, str]] = None
    locale: Optional[Any] = None
    headless: bool = True
    ff_version: Optional[int] = None
    ttl_seconds: int = 900
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "SessionRequest":
        return cls(
            os=payload.get("os"),
            config=dict(payload.get("config") or {}),
            window=list(payload["window"]) if payload.get("window") else None,
            fonts=list(payload.get("fonts") or []),
            custom_fonts_only=bool(payload.get("custom_fonts_only", False)),
            block_webgl=bool(payload.get("block_webgl", False)),
            webgl_config=list(payload["webgl_config"]) if payload.get("webgl_config") else None,
            proxy=dict(payload["proxy"]) if payload.get("proxy") else None,
            locale=payload.get("locale"),
            headless=bool(payload.get("headless", True)),
            ff_version=payload.get("ff_version"),
            ttl_seconds=int(payload.get("ttl_seconds", 900)),
            metadata=dict(payload.get("metadata") or {}),
        )

    def to_launch_kwargs(self) -> Dict[str, Any]:
        return {
            "config": self.config,
            "os": self.os,
            "window": _coerce_optional_tuple(self.window),
            "fonts": self.fonts or None,
            "custom_fonts_only": self.custom_fonts_only,
            "block_webgl": self.block_webgl,
            "webgl_config": _coerce_optional_tuple(self.webgl_config),
            "proxy": self.proxy,
            "locale": self.locale,
            "headless": self.headless,
            "ff_version": self.ff_version,
            "i_know_what_im_doing": False,
        }


@dataclass
class WorkerSlot:
    worker_id: str
    endpoint: str
    egress_classes: List[str] = field(default_factory=lambda: ["nss", "utls-sidecar"])
    active_sessions: int = 0

    def supports(self, egress_class: str) -> bool:
        return egress_class in self.egress_classes


@dataclass
class SessionLease:
    session_id: str
    worker_id: str
    worker_endpoint: str
    snapshot_key: str
    expires_at: float


# ── Abstract interfaces (Protocols) ──────────────────────────────────

@runtime_checkable
class SnapshotStore(Protocol):
    """Abstract interface for session snapshot persistence."""

    def save(self, snapshot_key: str, payload: Mapping[str, Any]) -> None:
        ...

    def load(self, snapshot_key: str) -> Optional[Dict[str, Any]]:
        ...

    def delete(self, snapshot_key: str) -> None:
        ...

    def health_check(self) -> bool:
        ...


@runtime_checkable
class PoolManager(Protocol):
    """Abstract interface for worker pool management."""

    def acquire(self, session_id: str, egress_class: str) -> WorkerSlot:
        ...

    def release(self, session_id: str) -> None:
        ...

    def health_check(self) -> bool:
        ...


# ── File-based implementations ──────────────────────────────────────

class FileSnapshotStore:
    """Snapshot store backed by the local filesystem."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, snapshot_key: str) -> Path:
        return self.root / f"{_sanitize_snapshot_key(snapshot_key)}.json"

    def save(self, snapshot_key: str, payload: Mapping[str, Any]) -> None:
        self._path(snapshot_key).write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), indent=2),
            encoding="utf-8",
        )

    def load(self, snapshot_key: str) -> Optional[Dict[str, Any]]:
        path = self._path(snapshot_key)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def delete(self, snapshot_key: str) -> None:
        path = self._path(snapshot_key)
        if path.exists():
            path.unlink()

    def health_check(self) -> bool:
        return self.root.is_dir()


class InMemoryPoolManager:
    """Worker pool manager with in-process state."""

    def __init__(
        self,
        *,
        pool_size: int = 4,
        worker_endpoints: Optional[Sequence[str]] = None,
    ) -> None:
        endpoints = list(worker_endpoints or [])
        if not endpoints:
            endpoints = [f"http://camoufox-worker-{index}:8000" for index in range(1, pool_size + 1)]
        self._lock = threading.Lock()
        self._workers = [
            WorkerSlot(worker_id=f"worker-{index + 1}", endpoint=endpoint)
            for index, endpoint in enumerate(endpoints)
        ]
        self._leases: Dict[str, str] = {}

    def acquire(self, session_id: str, egress_class: str) -> WorkerSlot:
        with self._lock:
            candidates = [worker for worker in self._workers if worker.supports(egress_class)]
            if not candidates:
                raise RuntimeError(f"No worker available for egress class '{egress_class}'")
            worker = min(candidates, key=lambda item: (item.active_sessions, item.worker_id))
            worker.active_sessions += 1
            self._leases[session_id] = worker.worker_id
            return WorkerSlot(
                worker_id=worker.worker_id,
                endpoint=worker.endpoint,
                egress_classes=list(worker.egress_classes),
                active_sessions=worker.active_sessions,
            )

    def release(self, session_id: str) -> None:
        with self._lock:
            worker_id = self._leases.pop(session_id, None)
            if not worker_id:
                return
            for worker in self._workers:
                if worker.worker_id == worker_id and worker.active_sessions > 0:
                    worker.active_sessions -= 1
                    return

    def health_check(self) -> bool:
        return True


# ── Redis-backed implementations ────────────────────────────────────

class RedisSnapshotStore:
    """
    Snapshot store backed by Redis for cross-pod session persistence.
    Requires the 'redis' package: pip install redis
    """

    def __init__(
        self,
        *,
        redis_url: str = "redis://localhost:6379/0",
        key_prefix: str = "camoufox:snapshot:",
        ttl_seconds: int = 3600,
    ) -> None:
        try:
            import redis as redis_lib
        except ImportError:
            raise ImportError(
                "Redis backend requires the 'redis' package. "
                "Install it with: pip install redis"
            )
        self._client = redis_lib.Redis.from_url(redis_url, decode_responses=True)
        self._prefix = key_prefix
        self._ttl = ttl_seconds

    def _key(self, snapshot_key: str) -> str:
        return f"{self._prefix}{snapshot_key}"

    def save(self, snapshot_key: str, payload: Mapping[str, Any]) -> None:
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        self._client.setex(self._key(snapshot_key), self._ttl, data)

    def load(self, snapshot_key: str) -> Optional[Dict[str, Any]]:
        data = self._client.get(self._key(snapshot_key))
        if data is None:
            return None
        return json.loads(data)

    def delete(self, snapshot_key: str) -> None:
        self._client.delete(self._key(snapshot_key))

    def health_check(self) -> bool:
        try:
            return self._client.ping()
        except Exception:
            return False


class RedisPoolManager:
    """
    Worker pool manager with Redis-backed state for cross-pod session affinity.
    Requires the 'redis' package: pip install redis
    """

    def __init__(
        self,
        *,
        redis_url: str = "redis://localhost:6379/0",
        key_prefix: str = "camoufox:pool:",
        pool_size: int = 4,
        worker_endpoints: Optional[Sequence[str]] = None,
    ) -> None:
        try:
            import redis as redis_lib
        except ImportError:
            raise ImportError(
                "Redis backend requires the 'redis' package. "
                "Install it with: pip install redis"
            )
        self._client = redis_lib.Redis.from_url(redis_url, decode_responses=True)
        self._prefix = key_prefix
        self._pool_size = pool_size

        endpoints = list(worker_endpoints or [])
        if not endpoints:
            endpoints = [
                f"http://camoufox-worker-{i}:8000"
                for i in range(1, pool_size + 1)
            ]

        # Initialize workers in Redis if not present
        for idx, endpoint in enumerate(endpoints):
            worker_id = f"worker-{idx + 1}"
            worker_key = f"{self._prefix}worker:{worker_id}"
            if not self._client.exists(worker_key):
                self._client.hset(worker_key, mapping={
                    "worker_id": worker_id,
                    "endpoint": endpoint,
                    "active_sessions": "0",
                    "egress_classes": "nss,utls-sidecar",
                })

    def acquire(self, session_id: str, egress_class: str) -> WorkerSlot:
        # Find the worker with fewest active sessions
        worker_keys = self._client.keys(f"{self._prefix}worker:*")
        best_worker = None
        best_sessions = float("inf")

        for key in worker_keys:
            data = self._client.hgetall(key)
            egress_classes = data.get("egress_classes", "").split(",")
            if egress_class not in egress_classes:
                continue
            sessions = int(data.get("active_sessions", "0"))
            if sessions < best_sessions:
                best_sessions = sessions
                best_worker = data

        if best_worker is None:
            raise RuntimeError(f"No worker available for egress class '{egress_class}'")

        worker_id = best_worker["worker_id"]
        worker_key = f"{self._prefix}worker:{worker_id}"
        self._client.hincrby(worker_key, "active_sessions", 1)
        self._client.hset(f"{self._prefix}lease:{session_id}", "worker_id", worker_id)

        return WorkerSlot(
            worker_id=worker_id,
            endpoint=best_worker["endpoint"],
            egress_classes=best_worker.get("egress_classes", "").split(","),
            active_sessions=int(best_worker.get("active_sessions", "0")) + 1,
        )

    def release(self, session_id: str) -> None:
        lease_key = f"{self._prefix}lease:{session_id}"
        worker_id = self._client.hget(lease_key, "worker_id")
        if not worker_id:
            return
        worker_key = f"{self._prefix}worker:{worker_id}"
        self._client.hincrby(worker_key, "active_sessions", -1)
        self._client.delete(lease_key)

    def health_check(self) -> bool:
        try:
            return self._client.ping()
        except Exception:
            return False


# ── S3-backed snapshot store ─────────────────────────────────────────

class S3SnapshotStore:
    """
    Snapshot store backed by S3/MinIO for cross-pod session persistence.
    Requires the 'boto3' package: pip install boto3
    """

    def __init__(
        self,
        *,
        bucket_name: str = "camoufox-snapshots",
        key_prefix: str = "snapshots/",
        endpoint_url: Optional[str] = None,
        region_name: str = "us-east-1",
    ) -> None:
        try:
            import boto3
        except ImportError:
            raise ImportError(
                "S3 backend requires the 'boto3' package. "
                "Install it with: pip install boto3"
            )
        kwargs: Dict[str, Any] = {"region_name": region_name}
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        self._s3 = boto3.client("s3", **kwargs)
        self._bucket = bucket_name
        self._prefix = key_prefix

    def _key(self, snapshot_key: str) -> str:
        return f"{self._prefix}{snapshot_key}.json"

    def save(self, snapshot_key: str, payload: Mapping[str, Any]) -> None:
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        self._s3.put_object(
            Bucket=self._bucket,
            Key=self._key(snapshot_key),
            Body=data.encode("utf-8"),
            ContentType="application/json",
        )

    def load(self, snapshot_key: str) -> Optional[Dict[str, Any]]:
        try:
            response = self._s3.get_object(
                Bucket=self._bucket,
                Key=self._key(snapshot_key),
            )
            data = response["Body"].read().decode("utf-8")
            return json.loads(data)
        except self._s3.exceptions.NoSuchKey:
            return None
        except Exception:
            return None

    def delete(self, snapshot_key: str) -> None:
        try:
            self._s3.delete_object(
                Bucket=self._bucket,
                Key=self._key(snapshot_key),
            )
        except Exception:
            pass

    def health_check(self) -> bool:
        try:
            self._s3.head_bucket(Bucket=self._bucket)
            return True
        except Exception:
            return False


# ── Session Broker ───────────────────────────────────────────────────

def _build_session_artifact(request: SessionRequest) -> Dict[str, Any]:
    from .utils import launch_options

    launch_kwargs = {
        key: value
        for key, value in request.to_launch_kwargs().items()
        if value is not None
    }
    return launch_options(**launch_kwargs)


def _network_metadata_from_artifact(artifact: Mapping[str, Any]) -> Dict[str, Any]:
    env = dict(artifact.get("env") or {})
    raw_profile = env.get("CAMOU_NET_PROFILE")
    if not raw_profile:
        return {"proxy_egress_class": "nss"}
    try:
        return json.loads(raw_profile)
    except json.JSONDecodeError:
        return {"proxy_egress_class": "nss"}


class SessionBroker:
    def __init__(
        self,
        *,
        snapshot_store: SnapshotStore,
        pool_manager: PoolManager,
        session_factory: Optional[Callable[[SessionRequest], Dict[str, Any]]] = None,
        now: Optional[Callable[[], float]] = None,
    ) -> None:
        self.snapshot_store = snapshot_store
        self.pool_manager = pool_manager
        self.session_factory = session_factory or _build_session_artifact
        self._now = now or time.time
        self._leases: Dict[str, SessionLease] = {}
        self._lock = threading.Lock()

    def create_session(self, payload: Mapping[str, Any]) -> SessionLease:
        request = SessionRequest.from_payload(payload)
        if request.ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")

        artifact = self.session_factory(request)
        network_metadata = _network_metadata_from_artifact(artifact)
        egress_class = network_metadata.get("proxy_egress_class", "nss")
        session_id = uuid.uuid4().hex
        snapshot_key = _sanitize_snapshot_key(request.metadata.get("snapshot_key") or session_id)
        lease = self._issue_lease(session_id, snapshot_key, request.ttl_seconds, egress_class)

        snapshot_payload = {
            "session_id": session_id,
            "snapshot_key": snapshot_key,
            "request": asdict(request),
            "launch_options": artifact,
            "network_profile": network_metadata,
            "worker": {
                "worker_id": lease.worker_id,
                "worker_endpoint": lease.worker_endpoint,
            },
            "created_at": self._now(),
        }
        try:
            self.snapshot_store.save(snapshot_key, snapshot_payload)
        except Exception:
            with self._lock:
                self._leases.pop(session_id, None)
            self.pool_manager.release(session_id)
            raise
        return lease

    def _issue_lease(
        self,
        session_id: str,
        snapshot_key: str,
        ttl_seconds: int,
        egress_class: str,
    ) -> SessionLease:
        worker = self.pool_manager.acquire(session_id, egress_class)
        lease = SessionLease(
            session_id=session_id,
            worker_id=worker.worker_id,
            worker_endpoint=worker.endpoint,
            snapshot_key=snapshot_key,
            expires_at=self._now() + ttl_seconds,
        )
        with self._lock:
            self._leases[session_id] = lease
        return lease

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            lease = self._leases.get(session_id)
        if not lease:
            return None
        if lease.expires_at <= self._now():
            self.release_session(session_id)
            return None
        snapshot = self.snapshot_store.load(lease.snapshot_key)
        if not snapshot:
            return None
        return {
            "lease": asdict(lease),
            "snapshot": snapshot,
        }

    def release_session(self, session_id: str) -> bool:
        with self._lock:
            lease = self._leases.pop(session_id, None)
        if not lease:
            return False
        self.pool_manager.release(session_id)
        self.snapshot_store.delete(lease.snapshot_key)
        return True

    def reap_expired(self) -> int:
        now = self._now()
        with self._lock:
            expired = [
                session_id
                for session_id, lease in self._leases.items()
                if lease.expires_at <= now
            ]
        for session_id in expired:
            self.release_session(session_id)
        return len(expired)


# ── HTTP Server ──────────────────────────────────────────────────────

def _json_response(handler: BaseHTTPRequestHandler, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _empty_response(handler: BaseHTTPRequestHandler, status: HTTPStatus) -> None:
    handler.send_response(status)
    handler.send_header("Content-Length", "0")
    handler.end_headers()


def _make_handler(broker: SessionBroker):
    expected_token = os.environ.get("CAMOUFOX_BROKER_TOKEN", "")
    class SessionBrokerHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/healthz":
                expired_reaped = broker.reap_expired()
                store_ok = broker.snapshot_store.health_check()
                pool_ok = broker.pool_manager.health_check()
                status = HTTPStatus.OK if (store_ok and pool_ok) else HTTPStatus.SERVICE_UNAVAILABLE
                _json_response(self, status, {
                    "ok": store_ok and pool_ok,
                    "store_backend": type(broker.snapshot_store).__name__,
                    "store_healthy": store_ok,
                    "pool_backend": type(broker.pool_manager).__name__,
                    "pool_healthy": pool_ok,
                    "expired_reaped": expired_reaped,
                })
                return

            if expected_token:
                auth_header = self.headers.get("Authorization", "")
                if not auth_header.startswith("Bearer ") or auth_header[7:] != expected_token:
                    _json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                    return

            if self.path.startswith("/sessions/"):
                session_id = self.path.rsplit("/", 1)[-1]
                payload = broker.get_session(session_id)
                if payload is None:
                    _json_response(self, HTTPStatus.NOT_FOUND, {"error": "session_not_found"})
                    return
                _json_response(self, HTTPStatus.OK, payload)
                return

            _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:
            if self.path != "/sessions":
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return

            if expected_token:
                auth_header = self.headers.get("Authorization", "")
                if not auth_header.startswith("Bearer ") or auth_header[7:] != expected_token:
                    _json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                    return

            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length < 0:
                    raise ValueError("Content-Length must be non-negative")
                if content_length > MAX_REQUEST_BODY_BYTES:
                    _json_response(
                        self,
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        {"error": "request_too_large"},
                    )
                    return
                payload = json.loads(self.rfile.read(content_length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("request body must be a JSON object")
                lease = broker.create_session(payload)
            except (json.JSONDecodeError, ValueError) as exc:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": str(exc)})
                return
            _json_response(
                self,
                HTTPStatus.CREATED,
                {
                    "session_id": lease.session_id,
                    "worker_id": lease.worker_id,
                    "worker_endpoint": lease.worker_endpoint,
                    "snapshot_key": lease.snapshot_key,
                    "expires_at": lease.expires_at,
                },
            )

        def do_DELETE(self) -> None:
            if not self.path.startswith("/sessions/"):
                _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return

            if expected_token:
                auth_header = self.headers.get("Authorization", "")
                if not auth_header.startswith("Bearer ") or auth_header[7:] != expected_token:
                    _json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                    return

            session_id = self.path.rsplit("/", 1)[-1]
            if broker.release_session(session_id):
                _empty_response(self, HTTPStatus.NO_CONTENT)
                return
            _json_response(self, HTTPStatus.NOT_FOUND, {"error": "session_not_found"})

        def log_message(self, format: str, *args: Any) -> None:
            return

    return SessionBrokerHandler


# ── Backend factory ──────────────────────────────────────────────────

def _create_snapshot_store(backend: str) -> SnapshotStore:
    """Create a snapshot store based on the backend identifier."""
    if backend == "redis":
        redis_url = os.environ.get("CAMOUFOX_REDIS_URL", "redis://localhost:6379/0")
        return RedisSnapshotStore(redis_url=redis_url)
    elif backend == "s3":
        return S3SnapshotStore(
            bucket_name=os.environ.get("CAMOUFOX_S3_BUCKET", "camoufox-snapshots"),
            endpoint_url=os.environ.get("CAMOUFOX_S3_ENDPOINT"),
            key_prefix=os.environ.get("CAMOUFOX_S3_PREFIX", "snapshots/"),
        )
    else:
        snapshot_dir = os.environ.get("CAMOUFOX_SNAPSHOT_DIR", ".camoufox-snapshots")
        return FileSnapshotStore(Path(snapshot_dir))


def _create_pool_manager(
    backend: str,
    pool_size: int,
    worker_endpoints: Optional[List[str]],
) -> PoolManager:
    """Create a pool manager based on the backend identifier."""
    if backend == "redis":
        redis_url = os.environ.get("CAMOUFOX_REDIS_URL", "redis://localhost:6379/0")
        return RedisPoolManager(
            redis_url=redis_url,
            pool_size=pool_size,
            worker_endpoints=worker_endpoints,
        )
    else:
        return InMemoryPoolManager(
            pool_size=pool_size,
            worker_endpoints=worker_endpoints,
        )


def serve_broker(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    snapshot_dir: Optional[str] = None,
    pool_size: int = 4,
) -> None:
    """
    Start the session broker HTTP server.

    Backend selection is controlled by environment variables:
      - CAMOUFOX_STORE_BACKEND: "file" (default), "redis", or "s3"
      - CAMOUFOX_POOL_BACKEND: "memory" (default) or "redis"
      - CAMOUFOX_REDIS_URL: Redis connection URL
      - CAMOUFOX_S3_BUCKET: S3 bucket name
      - CAMOUFOX_S3_ENDPOINT: S3/MinIO endpoint URL
      - CAMOUFOX_POOL_ENDPOINTS: Comma-separated worker endpoints
    """
    store_backend = os.environ.get("CAMOUFOX_STORE_BACKEND", "file")
    pool_backend = os.environ.get("CAMOUFOX_POOL_BACKEND", "memory")

    # Override snapshot dir from param if provided
    if snapshot_dir:
        os.environ.setdefault("CAMOUFOX_SNAPSHOT_DIR", snapshot_dir)

    worker_endpoints_raw = os.environ.get("CAMOUFOX_POOL_ENDPOINTS")
    worker_endpoints = worker_endpoints_raw.split(",") if worker_endpoints_raw else None

    snapshot_store = _create_snapshot_store(store_backend)
    pool_manager = _create_pool_manager(pool_backend, pool_size, worker_endpoints)

    logger.info(
        "Starting broker: store=%s pool=%s host=%s port=%d",
        type(snapshot_store).__name__,
        type(pool_manager).__name__,
        host,
        port,
    )

    broker = SessionBroker(
        snapshot_store=snapshot_store,
        pool_manager=pool_manager,
    )
    server = ThreadingHTTPServer((host, port), _make_handler(broker))
    try:
        server.serve_forever()
    finally:
        server.server_close()
