__all__ = [
    "Camoufox",
    "NewBrowser",
    "AsyncCamoufox",
    "AsyncNewBrowser",
    "DefaultAddons",
    "IdentityCoherenceEngine",
    "IdentityState",
    "SessionBroker",
    "FileSnapshotStore",
    "InMemoryPoolManager",
    "serve_broker",
    "launch_options",
]


def __getattr__(name):
    if name in {"Camoufox", "NewBrowser"}:
        from .sync_api import Camoufox, NewBrowser
        return {"Camoufox": Camoufox, "NewBrowser": NewBrowser}[name]
    if name in {"AsyncCamoufox", "AsyncNewBrowser"}:
        from .async_api import AsyncCamoufox, AsyncNewBrowser
        return {"AsyncCamoufox": AsyncCamoufox, "AsyncNewBrowser": AsyncNewBrowser}[name]
    if name == "DefaultAddons":
        from .addons import DefaultAddons
        return DefaultAddons
    if name in {"IdentityCoherenceEngine", "IdentityState"}:
        from .identity import IdentityCoherenceEngine, IdentityState
        return {"IdentityCoherenceEngine": IdentityCoherenceEngine, "IdentityState": IdentityState}[name]
    if name == "launch_options":
        from .utils import launch_options
        return launch_options
    if name in {"SessionBroker", "FileSnapshotStore", "InMemoryPoolManager", "serve_broker"}:
        from .cloud_native import (
            FileSnapshotStore,
            InMemoryPoolManager,
            SessionBroker,
            serve_broker,
        )
        return {
            "SessionBroker": SessionBroker,
            "FileSnapshotStore": FileSnapshotStore,
            "InMemoryPoolManager": InMemoryPoolManager,
            "serve_broker": serve_broker,
        }[name]
    raise AttributeError(f"module 'camoufox' has no attribute {name!r}")
