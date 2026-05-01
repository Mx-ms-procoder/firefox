"""
Identity coherence engine for Camoufox.

The engine compiles a single session state that feeds all spoofed browser
surfaces. Instead of sampling BrowserForge, fonts, WebGL, screen, and audio
independently, it derives them from one deterministic identity seed and one
OS-scoped device profile.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from random import Random
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Sequence, Tuple

import orjson

from .device_profiles import DeviceProfile, build_font_list, sample_device_profile
from .device_profiles.coherence import validate_coherence
from .network_profile import NetworkProfile
from .tls_profiles import get_tls_profile

if TYPE_CHECKING:
    from browserforge.fingerprints import Fingerprint

_IDENTITY_PREFIXES = (
    "navigator.",
    "screen.",
    "window.",
    "document.body.",
    "AudioContext:",
    "webGl:",
    "webGl2:",
    "fonts",
    "fonts:",
    "canvas:",
)

_CHROME_WIDTH_BOUNDS = {
    "win": (12, 32),
    "mac": (0, 24),
    "lin": (8, 32),
}

_CHROME_HEIGHT_BOUNDS = {
    "win": (72, 160),
    "mac": (64, 140),
    "lin": (72, 152),
}


@dataclass(frozen=True)
class WindowMetrics:
    screen_x: int
    screen_y: int
    outer_width: int
    outer_height: int
    inner_width: int
    inner_height: int
    client_width: int
    client_height: int
    page_x_offset: float = 0.0
    page_y_offset: float = 0.0

    def as_config(self) -> Dict[str, Any]:
        return {
            "window.screenX": self.screen_x,
            "window.screenY": self.screen_y,
            "window.outerWidth": self.outer_width,
            "window.outerHeight": self.outer_height,
            "window.innerWidth": self.inner_width,
            "window.innerHeight": self.inner_height,
            "document.body.clientLeft": 0,
            "document.body.clientTop": 0,
            "document.body.clientWidth": self.client_width,
            "document.body.clientHeight": self.client_height,
            "screen.pageXOffset": self.page_x_offset,
            "screen.pageYOffset": self.page_y_offset,
        }


@dataclass(frozen=True)
class IdentityState:
    profile_id: str
    seed: int
    target_os: str
    device_profile: DeviceProfile
    window_metrics: WindowMetrics
    config: Dict[str, Any]
    firefox_user_prefs: Dict[str, Any]
    network_profile: NetworkProfile
    coherence_issues: Tuple[str, ...]
    manual_overrides: Tuple[str, ...]


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _is_identity_key(key: str) -> bool:
    return key.startswith(_IDENTITY_PREFIXES) or key == "fonts"


class IdentityCoherenceEngine:
    """
    Compile one deterministic session identity for all spoofed surfaces.

    Derived values intentionally share the same seed so the final session can
    be regenerated without cross-surface drift.
    """

    def build(
        self,
        *,
        fingerprint: "Fingerprint",
        ff_version: str,
        target_os: str,
        user_config: Optional[Mapping[str, Any]] = None,
        window: Optional[Tuple[int, int]] = None,
        fonts: Optional[Sequence[str]] = None,
        custom_fonts_only: bool = False,
        webgl_enabled: bool = True,
        webgl_config: Optional[Tuple[str, str]] = None,
    ) -> IdentityState:
        from .fingerprints import from_browserforge

        return self.build_from_base_config(
            base_config=from_browserforge(fingerprint, ff_version),
            target_os=target_os,
            user_config=user_config,
            window=window,
            fonts=fonts,
            custom_fonts_only=custom_fonts_only,
            webgl_enabled=webgl_enabled,
            webgl_config=webgl_config,
        )

    def build_from_base_config(
        self,
        *,
        base_config: Mapping[str, Any],
        target_os: str,
        user_config: Optional[Mapping[str, Any]] = None,
        window: Optional[Tuple[int, int]] = None,
        fonts: Optional[Sequence[str]] = None,
        custom_fonts_only: bool = False,
        webgl_enabled: bool = True,
        webgl_config: Optional[Tuple[str, str]] = None,
    ) -> IdentityState:
        payload = self._seed_payload(
            base_config=base_config,
            target_os=target_os,
            user_config=user_config,
            window=window,
            fonts=fonts,
            custom_fonts_only=custom_fonts_only,
            webgl_enabled=webgl_enabled,
            webgl_config=webgl_config,
        )
        digest = hashlib.sha256(
            orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
        ).digest()
        seed = int.from_bytes(digest[:8], "big")
        generator = Random(seed)

        screen_hint = self._screen_hint(base_config)
        device_profile = sample_device_profile(
            target_os,
            rng=generator,
            webgl_enabled=webgl_enabled,
            webgl_config=webgl_config,
            screen_hint=screen_hint,
        )
        window_metrics = self._resolve_window_metrics(
            base_config=base_config,
            device_profile=device_profile,
            requested_window=window,
            generator=generator,
        )

        compiled_config = dict(base_config)
        compiled_config.update(device_profile.to_camoufox_config())
        compiled_config.update(window_metrics.as_config())
        compiled_config["fonts"] = build_font_list(
            target_os,
            extra_fonts=fonts,
            custom_only=custom_fonts_only,
        )
        compiled_config["fonts:spacing_seed"] = (
            int.from_bytes(digest[8:12], "big") % 1_073_741_823
        )
        compiled_config["canvas:aaOffset"] = int(
            max(-24, min(24, round(generator.gauss(0.0, 8.0))))
        )
        compiled_config["canvas:aaCapOffset"] = True
        compiled_config["window.history.length"] = 1 + (digest[13] % 5)

        firefox_user_prefs: Dict[str, Any] = {}
        if webgl_enabled:
            firefox_user_prefs.update(
                {
                    "webgl.enable-webgl2": device_profile.enable_webgl2,
                    "webgl.force-enabled": True,
                }
            )

        manual_overrides = self._manual_override_conflicts(
            compiled_config=compiled_config,
            user_config=user_config,
        )

        from ua_parser import user_agent_parser
        ua = base_config.get("navigator.userAgent", "")
        ua_parsed = user_agent_parser.ParseUserAgent(ua)
        
        # Camoufox is Firefox-only. Enforce pure Firefox profile even if user overrides UA.
        family_key = "firefox"
        try:
            major_version = int(ua_parsed.get("major", "135"))
        except ValueError:
            major_version = 135

        network_profile = get_tls_profile(family_key, major_version)
        if not network_profile:
            network_profile = get_tls_profile("firefox", 135)  # Fallback

        return IdentityState(
            profile_id=digest.hex()[:16],
            seed=seed,
            target_os=target_os,
            device_profile=device_profile,
            window_metrics=window_metrics,
            config=compiled_config,
            firefox_user_prefs=firefox_user_prefs,
            network_profile=network_profile,
            coherence_issues=tuple(validate_coherence(device_profile)),
            manual_overrides=tuple(manual_overrides),
        )

    def _seed_payload(
        self,
        *,
        base_config: Mapping[str, Any],
        target_os: str,
        user_config: Optional[Mapping[str, Any]],
        window: Optional[Tuple[int, int]],
        fonts: Optional[Sequence[str]],
        custom_fonts_only: bool,
        webgl_enabled: bool,
        webgl_config: Optional[Tuple[str, str]],
    ) -> Dict[str, Any]:
        return {
            "target_os": target_os,
            "base": {
                key: base_config[key]
                for key in sorted(base_config)
                if _is_identity_key(key)
            },
            "manual": {
                key: user_config[key]
                for key in sorted(user_config or {})
                if _is_identity_key(key)
            },
            "window": list(window) if window else None,
            "fonts": sorted(fonts or []),
            "custom_fonts_only": custom_fonts_only,
            "webgl_enabled": webgl_enabled,
            "webgl_config": list(webgl_config) if webgl_config else None,
        }

    def _screen_hint(
        self, base_config: Mapping[str, Any]
    ) -> Optional[Tuple[int, int]]:
        width = base_config.get("screen.width")
        height = base_config.get("screen.height")
        if isinstance(width, int) and isinstance(height, int):
            return width, height
        return None

    def _resolve_window_metrics(
        self,
        *,
        base_config: Mapping[str, Any],
        device_profile: DeviceProfile,
        requested_window: Optional[Tuple[int, int]],
        generator: Random,
    ) -> WindowMetrics:
        avail_width = device_profile.screen_width
        avail_height = max(device_profile.screen_height - device_profile.taskbar_height, 0)
        chrome_width = self._chrome_width(
            base_config=base_config,
            target_os=device_profile.os_family,
        )
        chrome_height = self._chrome_height(
            base_config=base_config,
            target_os=device_profile.os_family,
        )

        if requested_window:
            outer_width = max(640, min(requested_window[0], avail_width))
            outer_height = max(480, min(requested_window[1], max(avail_height, 480)))
        else:
            outer_width, outer_height = self._sample_window_size(
                base_config=base_config,
                device_profile=device_profile,
                avail_width=avail_width,
                avail_height=avail_height,
                generator=generator,
            )

        inner_width = max(200, outer_width - chrome_width)
        inner_height = max(200, outer_height - chrome_height)

        free_x = max(avail_width - outer_width, 0)
        free_y = max(avail_height - outer_height, 0)
        screen_x = int(round(free_x * (0.32 + (0.36 * generator.random()))))
        screen_y = int(round(free_y * (0.14 + (0.28 * generator.random()))))

        return WindowMetrics(
            screen_x=screen_x,
            screen_y=screen_y,
            outer_width=outer_width,
            outer_height=outer_height,
            inner_width=inner_width,
            inner_height=inner_height,
            client_width=inner_width,
            client_height=inner_height,
        )

    def _sample_window_size(
        self,
        *,
        base_config: Mapping[str, Any],
        device_profile: DeviceProfile,
        avail_width: int,
        avail_height: int,
        generator: Random,
    ) -> Tuple[int, int]:
        base_screen_width = int(base_config.get("screen.width", device_profile.screen_width) or 1)
        base_screen_height = int(base_config.get("screen.height", device_profile.screen_height) or 1)
        base_outer_width = int(base_config.get("window.outerWidth", 0) or 0)
        base_outer_height = int(base_config.get("window.outerHeight", 0) or 0)

        if base_outer_width > 0 and base_outer_height > 0:
            width_ratio = _clip(base_outer_width / max(base_screen_width, 1), 0.62, 0.95)
            height_ratio = _clip(base_outer_height / max(base_screen_height, 1), 0.58, 0.94)
        else:
            width_ratio = 0.72 + (0.18 * generator.random())
            height_ratio = 0.68 + (0.16 * generator.random())

        outer_width = min(avail_width, max(960, int(round(avail_width * width_ratio))))
        outer_height = min(avail_height, max(720, int(round(avail_height * height_ratio))))
        return outer_width, outer_height

    def _chrome_width(
        self, *, base_config: Mapping[str, Any], target_os: str
    ) -> int:
        delta = int(
            (base_config.get("window.outerWidth", 0) or 0)
            - (base_config.get("window.innerWidth", 0) or 0)
        )
        lower, upper = _CHROME_WIDTH_BOUNDS[target_os]
        return int(_clip(delta if delta > 0 else lower, lower, upper))

    def _chrome_height(
        self, *, base_config: Mapping[str, Any], target_os: str
    ) -> int:
        delta = int(
            (base_config.get("window.outerHeight", 0) or 0)
            - (base_config.get("window.innerHeight", 0) or 0)
        )
        lower, upper = _CHROME_HEIGHT_BOUNDS[target_os]
        return int(_clip(delta if delta > 0 else lower, lower, upper))

    def _manual_override_conflicts(
        self,
        *,
        compiled_config: Mapping[str, Any],
        user_config: Optional[Mapping[str, Any]],
    ) -> Sequence[str]:
        conflicts = []
        for key, value in (user_config or {}).items():
            if not _is_identity_key(key):
                continue
            if key in compiled_config and compiled_config[key] != value:
                conflicts.append(key)
        return conflicts


def _extract_subsystem(config: Mapping[str, Any], prefix: str) -> Dict[str, Any]:
    """Extract all keys with the given prefix into a sub-dictionary."""
    result: Dict[str, Any] = {}
    for key, value in config.items():
        if key.startswith(prefix):
            sub_key = key[len(prefix):]
            result[sub_key] = value
    return result


def to_identity_blob(state: IdentityState) -> Dict[str, Any]:
    """
    Serialize the complete IdentityState as a single nested JSON structure
    (the "IdentityBlob") covering all spoofed subsystems.

    This blob is the canonical "source of truth" that feeds CAMOU_CONFIG
    and can be round-tripped or inspected for debugging.
    """
    config = state.config

    # Navigator subsystem
    navigator_keys = (
        "userAgent", "appCodeName", "appName", "appVersion", "buildID",
        "language", "languages", "platform", "oscpu", "product",
        "productSub", "doNotTrack", "globalPrivacyControl",
        "hardwareConcurrency", "maxTouchPoints",
    )
    navigator_blob: Dict[str, Any] = {}
    for key in navigator_keys:
        full_key = f"navigator.{key}"
        if full_key in config:
            navigator_blob[key] = config[full_key]

    # Display subsystem (screen + window + document.body)
    display_blob: Dict[str, Any] = {
        "screen": {},
        "window": {},
        "documentBody": {},
    }
    for key, value in config.items():
        if key.startswith("screen."):
            display_blob["screen"][key.split(".", 1)[1]] = value
        elif key.startswith("window."):
            display_blob["window"][key.split(".", 1)[1]] = value
        elif key.startswith("document.body."):
            display_blob["documentBody"][key.split(".", 2)[2]] = value

    # WebGL subsystem
    webgl_blob: Dict[str, Any] = {}
    for key, value in config.items():
        if key.startswith("webGl:"):
            webgl_blob[key[6:]] = value
    webgl2_blob: Dict[str, Any] = {}
    for key, value in config.items():
        if key.startswith("webGl2:"):
            webgl2_blob[key[7:]] = value

    # Audio subsystem
    audio_blob: Dict[str, Any] = {}
    for key, value in config.items():
        if key.startswith("AudioContext:"):
            audio_blob[key[13:]] = value

    # Canvas subsystem
    canvas_blob: Dict[str, Any] = {}
    for key, value in config.items():
        if key.startswith("canvas:"):
            canvas_blob[key[7:]] = value

    # Font subsystem
    fonts_blob: Dict[str, Any] = {
        "list": config.get("fonts", []),
    }
    if "fonts:spacing_seed" in config:
        fonts_blob["spacing_seed"] = config["fonts:spacing_seed"]

    # Network subsystem
    network_blob: Dict[str, Any] = {}
    if state.network_profile:
        network_blob = state.network_profile.to_metadata()

    # Meta
    meta_blob: Dict[str, Any] = {
        "profile_id": state.profile_id,
        "seed": state.seed,
        "target_os": state.target_os,
        "performance_tier": state.device_profile.performance_tier,
        "coherence_issues": list(state.coherence_issues),
        "manual_overrides": list(state.manual_overrides),
    }

    return {
        "navigator": navigator_blob,
        "display": display_blob,
        "webgl": webgl_blob,
        "webgl2": webgl2_blob,
        "audio": audio_blob,
        "canvas": canvas_blob,
        "fonts": fonts_blob,
        "network": network_blob,
        "meta": meta_blob,
    }


def validate_identity_blob(blob: Dict[str, Any]) -> List[str]:
    """
    Cross-subsystem coherence validation on a serialized IdentityBlob.

    Returns a list of human-readable issue descriptions.
    Empty list means the blob is fully coherent.
    """
    issues: List[str] = []

    display = blob.get("display", {})
    screen = display.get("screen", {})
    window = display.get("window", {})
    webgl = blob.get("webgl", {})
    navigator = blob.get("navigator", {})
    audio = blob.get("audio", {})
    network = blob.get("network", {})

    # 1. WebGL MAX_VIEWPORT_DIMS must be >= screen resolution
    screen_width = screen.get("width")
    screen_height = screen.get("height")
    if screen_width and screen_height:
        # Check webgl parameters for MAX_VIEWPORT_DIMS (pname 3386 = 0x0D3A)
        params = webgl.get("parameters", {})
        viewport_dims = params.get("3386")
        if isinstance(viewport_dims, list) and len(viewport_dims) >= 2:
            if viewport_dims[0] < screen_width or viewport_dims[1] < screen_height:
                issues.append(
                    f"WebGL MAX_VIEWPORT_DIMS ({viewport_dims}) < screen "
                    f"resolution ({screen_width}x{screen_height})"
                )

    # 2. inner dimensions must be <= outer dimensions
    inner_w = window.get("innerWidth")
    inner_h = window.get("innerHeight")
    outer_w = window.get("outerWidth")
    outer_h = window.get("outerHeight")
    if inner_w and outer_w and inner_w > outer_w:
        issues.append(
            f"innerWidth ({inner_w}) > outerWidth ({outer_w})"
        )
    if inner_h and outer_h and inner_h > outer_h:
        issues.append(
            f"innerHeight ({inner_h}) > outerHeight ({outer_h})"
        )

    # 3. outer dimensions must be <= screen dimensions
    if outer_w and screen_width and outer_w > screen_width:
        issues.append(
            f"outerWidth ({outer_w}) > screen.width ({screen_width})"
        )
    if outer_h and screen_height and outer_h > screen_height:
        issues.append(
            f"outerHeight ({outer_h}) > screen.height ({screen_height})"
        )

    # 4. availWidth/availHeight should be <= screen width/height
    avail_w = screen.get("availWidth")
    avail_h = screen.get("availHeight")
    if avail_w and screen_width and avail_w > screen_width:
        issues.append(
            f"screen.availWidth ({avail_w}) > screen.width ({screen_width})"
        )
    if avail_h and screen_height and avail_h > screen_height:
        issues.append(
            f"screen.availHeight ({avail_h}) > screen.height ({screen_height})"
        )

    # 5. Audio sample rate must be a standard value
    sample_rate = audio.get("sampleRate")
    if sample_rate and sample_rate not in (8000, 16000, 22050, 44100, 48000, 96000):
        issues.append(
            f"Non-standard audio sample rate: {sample_rate}"
        )

    # 6. Network profile family must match navigator UA
    ua = navigator.get("userAgent", "")
    net_family = network.get("browser_family", "")
    if ua and net_family:
        ua_lower = ua.lower()
        if net_family == "firefox" and "firefox" not in ua_lower:
            issues.append(
                f"Network profile is '{net_family}' but UA does not contain 'firefox'"
            )
        elif net_family == "chrome" and "chrome" not in ua_lower:
            issues.append(
                f"Network profile is '{net_family}' but UA does not contain 'chrome'"
            )

    # 7. Canvas seed should be present when canvas is configured
    canvas = blob.get("canvas", {})
    if canvas.get("aaOffset") is not None and canvas.get("noiseSeed") is None:
        # Not strictly an error — noiseSeed is optional
        pass

    return issues

