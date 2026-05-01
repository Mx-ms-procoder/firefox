"""
Cross-validation rules for DeviceProfile coherence.

These rules catch implausible fingerprint combinations that would
be flagged by advanced anti-bot systems doing cross-reference checks
(e.g., Apple GPU on Windows, HiDPI on a low-end GPU, etc.).
"""

from typing import Any, Callable, List, Tuple

# TYPE_CHECKING import to avoid circular imports
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import DeviceProfile


def validate_coherence(profile: 'DeviceProfile') -> List[str]:
    """
    Run all coherence rules against a DeviceProfile.

    Returns:
        List of human-readable issue descriptions.
        Empty list means the profile is fully coherent.
    """
    issues = []
    for name, rule_fn in COHERENCE_RULES:
        try:
            if not rule_fn(profile):
                issues.append(name)
        except Exception:
            pass  # Rule evaluation failure is not a coherence issue
    return issues


# ── GPU ↔ OS coherence ────────────────────────────────────────────────

def _gpu_os_apple(p: 'DeviceProfile') -> bool:
    """Apple GPUs (M1/M2/M3, Apple GPU) should only appear on macOS."""
    vendor = (p.gpu_vendor + " " + p.gpu_renderer).lower()
    if "apple" in vendor and p.os_family != "mac":
        return False
    return True


def _gpu_os_directx(p: 'DeviceProfile') -> bool:
    """Direct3D / ANGLE (D3D11) references should only appear on Windows."""
    renderer = p.gpu_renderer.lower()
    if "d3d11" in renderer and p.os_family != "win":
        return False
    return True


def _gpu_os_mesa(p: 'DeviceProfile') -> bool:
    """Mesa drivers (llvmpipe, radeonsi, iris) are Linux-only."""
    renderer = p.gpu_renderer.lower()
    mesa_markers = ("mesa", "llvmpipe", "radeonsi", "iris", "gallium")
    if any(m in renderer for m in mesa_markers) and p.os_family != "lin":
        return False
    return True


# ── Screen ↔ OS coherence ────────────────────────────────────────────

def _dpr_os(p: 'DeviceProfile') -> bool:
    """macOS almost always has DPR 2.0 (Retina). Windows/Linux rarely exceed 1.5."""
    if p.os_family == "mac" and p.device_pixel_ratio < 1.5:
        return False  # Non-Retina Macs are extremely rare in 2024+
    if p.os_family == "lin" and p.device_pixel_ratio > 2.0:
        return False  # Linux rarely uses high DPR
    return True


def _color_depth_os(p: 'DeviceProfile') -> bool:
    """macOS uses 30-bit color (10bpc). Windows/Linux typically use 24-bit."""
    if p.os_family == "mac" and p.color_depth not in (24, 30):
        return False
    if p.os_family != "mac" and p.color_depth not in (24, 32):
        return False
    return True


def _screen_resolution_plausible(p: 'DeviceProfile') -> bool:
    """Screen dimensions must be reasonable (no 0x0 or 50000x50000)."""
    if p.screen_width < 320 or p.screen_width > 7680:
        return False
    if p.screen_height < 240 or p.screen_height > 4320:
        return False
    return True


def _screen_aspect_ratio(p: 'DeviceProfile') -> bool:
    """Aspect ratio should be between 1:1 and 32:9 (super ultrawide)."""
    ratio = p.screen_width / max(p.screen_height, 1)
    if ratio < 1.0 or ratio > 3.6:
        return False
    return True


# ── Hardware ↔ plausibility ──────────────────────────────────────────

def _hardware_concurrency_valid(p: 'DeviceProfile') -> bool:
    """Core count must be a realistic power-of-2 or common count."""
    valid_counts = {1, 2, 4, 6, 8, 10, 12, 14, 16, 20, 24, 28, 32, 48, 64}
    return p.hardware_concurrency in valid_counts


def _hardware_concurrency_os(p: 'DeviceProfile') -> bool:
    """macOS minimum is typically 4 cores (no single-core Macs exist)."""
    if p.os_family == "mac" and p.hardware_concurrency < 4:
        return False
    return True


# ── Audio ↔ OS coherence ─────────────────────────────────────────────

def _audio_sample_rate_valid(p: 'DeviceProfile') -> bool:
    """Sample rate must be a standard value."""
    return p.audio_sample_rate in (8000, 16000, 22050, 44100, 48000, 96000)


def _audio_latency_os(p: 'DeviceProfile') -> bool:
    """Output latency ranges differ by OS audio subsystem."""
    if p.os_family == "win" and p.audio_output_latency > 0.05:
        return False  # WASAPI is typically low-latency
    if p.os_family == "mac" and p.audio_output_latency > 0.05:
        return False  # CoreAudio is low-latency
    return True


# ── Font ↔ OS coherence ──────────────────────────────────────────────

def _fonts_os_windows_markers(p: 'DeviceProfile') -> bool:
    """Windows-only fonts (Segoe UI, Calibri) must not appear on Mac/Linux."""
    win_only = {"Segoe UI", "Calibri", "Consolas", "Corbel"}
    if p.os_family != "win" and any(f in p.fonts for f in win_only):
        return False
    return True


def _fonts_os_mac_markers(p: 'DeviceProfile') -> bool:
    """macOS-only fonts must not appear on Windows/Linux."""
    mac_only = {"Helvetica Neue", "Lucida Grande", "Menlo", "Geneva", "Monaco"}
    if p.os_family != "mac" and any(f in p.fonts for f in mac_only):
        return False
    return True


def _fonts_os_linux_markers(p: 'DeviceProfile') -> bool:
    """Linux TOR fonts must not appear on Windows/macOS."""
    lin_only = {"Arimo", "Cousine", "Tinos"}
    if p.os_family != "lin" and any(f in p.fonts for f in lin_only):
        return False
    return True


def _fonts_not_empty(p: 'DeviceProfile') -> bool:
    """A profile must have at least some fonts."""
    return len(p.fonts) >= 5


# ── Platform string ↔ OS coherence ───────────────────────────────────

def _platform_os_match(p: 'DeviceProfile') -> bool:
    """navigator.platform must match the OS family."""
    expected = {
        "win": "Win32",
        "mac": "MacIntel",
        "lin": "Linux x86_64",
    }
    if p.platform and p.platform != expected.get(p.os_family, ""):
        return False
    return True


def _oscpu_os_match(p: 'DeviceProfile') -> bool:
    """navigator.oscpu must reference the correct OS."""
    if not p.oscpu:
        return True
    if p.os_family == "win" and "Windows" not in p.oscpu:
        return False
    if p.os_family == "mac" and "Mac" not in p.oscpu:
        return False
    if p.os_family == "lin" and "Linux" not in p.oscpu:
        return False
    return True


# ── Screen ↔ GPU coherence ───────────────────────────────────────────

def _4k_needs_decent_gpu(p: 'DeviceProfile') -> bool:
    """4K+ resolution is implausible with integrated/software GPUs."""
    if p.screen_width >= 3840:
        renderer_lower = p.gpu_renderer.lower()
        software_markers = ("llvmpipe", "swiftshader", "software", "swrast")
        if any(m in renderer_lower for m in software_markers):
            return False
    return True


# ── Comprehensive rule registry ──────────────────────────────────────

COHERENCE_RULES: List[Tuple[str, Callable[['DeviceProfile'], bool]]] = [
    # GPU ↔ OS
    ("Apple GPU on non-macOS", _gpu_os_apple),
    ("D3D11 renderer on non-Windows", _gpu_os_directx),
    ("Mesa driver on non-Linux", _gpu_os_mesa),

    # Screen ↔ OS
    ("Non-Retina DPR on macOS", _dpr_os),
    ("Invalid color depth for OS", _color_depth_os),
    ("Screen resolution out of range", _screen_resolution_plausible),
    ("Implausible aspect ratio", _screen_aspect_ratio),

    # Hardware
    ("Invalid hardware concurrency value", _hardware_concurrency_valid),
    ("Too few cores for macOS", _hardware_concurrency_os),

    # Audio ↔ OS
    ("Invalid audio sample rate", _audio_sample_rate_valid),
    ("Implausible audio latency for OS", _audio_latency_os),

    # Fonts ↔ OS
    ("Windows-only fonts on non-Windows", _fonts_os_windows_markers),
    ("macOS-only fonts on non-macOS", _fonts_os_mac_markers),
    ("Linux-only fonts on non-Linux", _fonts_os_linux_markers),
    ("Too few fonts in profile", _fonts_not_empty),

    # Platform strings ↔ OS
    ("Platform string does not match OS", _platform_os_match),
    ("oscpu does not match OS", _oscpu_os_match),

    # Screen ↔ GPU
    ("4K resolution with software GPU", _4k_needs_decent_gpu),
]
