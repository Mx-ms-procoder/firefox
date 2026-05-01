"""
Correlated device profile sampling for Camoufox.

This module acts as the data plane behind the identity coherence engine.
It samples screen, hardware, audio, font, and WebGL characteristics from
the same OS-scoped profile family so the resulting session state is
internally consistent before it is serialized into MaskConfig.
"""

from dataclasses import dataclass, field
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Optional, Sequence, Tuple

import orjson

from .coherence import validate_coherence

_FONTS_PATH = Path(__file__).resolve().parents[1] / "fonts.json"

_SCREEN_PROFILES = {
    "win": {
        "low": [
            (1366, 768, 1.0, 24),
            (1536, 864, 1.0, 24),
            (1600, 900, 1.0, 24),
            (1920, 1080, 1.0, 24),
        ],
        "mid": [
            (1920, 1080, 1.0, 24),
            (1920, 1080, 1.25, 24),
            (2560, 1440, 1.0, 24),
            (2560, 1080, 1.0, 24),
            (2560, 1440, 1.25, 24),
        ],
        "high": [
            (2560, 1440, 1.25, 24),
            (3440, 1440, 1.0, 24),
            (3840, 2160, 1.5, 24),
            (3840, 2160, 1.25, 24),
        ],
    },
    "mac": {
        "low": [
            (1280, 800, 2.0, 30),
            (1440, 900, 2.0, 30),
            (1680, 1050, 2.0, 30),
        ],
        "mid": [
            (1680, 1050, 2.0, 30),
            (1920, 1080, 2.0, 30),
            (2560, 1440, 2.0, 30),
            (2560, 1600, 2.0, 30),
        ],
        "high": [
            (2560, 1600, 2.0, 30),
            (3024, 1964, 2.0, 30),
            (3456, 2234, 2.0, 30),
        ],
    },
    "lin": {
        "low": [
            (1280, 720, 1.0, 24),
            (1366, 768, 1.0, 24),
            (1600, 900, 1.0, 24),
        ],
        "mid": [
            (1600, 900, 1.0, 24),
            (1920, 1080, 1.0, 24),
            (1920, 1200, 1.0, 24),
            (2560, 1440, 1.0, 24),
        ],
        "high": [
            (2560, 1440, 1.0, 24),
            (3440, 1440, 1.0, 24),
            (3840, 2160, 1.0, 24),
        ],
    },
}

_CPU_PROFILES = {
    "win": {
        "low": [4, 4, 6, 8],
        "mid": [8, 8, 12, 16],
        "high": [16, 20, 24, 32],
    },
    "mac": {
        "low": [4, 4, 8],
        "mid": [8, 10, 10, 12],
        "high": [12, 16, 16],
    },
    "lin": {
        "low": [2, 4, 4, 8],
        "mid": [8, 8, 16],
        "high": [16, 32, 32],
    },
}

_AUDIO_PROFILES = {
    "win": {
        "low": [
            {"sampleRate": 44100, "channelCount": 2, "outputLatency": 0.014},
            {"sampleRate": 48000, "channelCount": 2, "outputLatency": 0.012},
        ],
        "mid": [
            {"sampleRate": 48000, "channelCount": 2, "outputLatency": 0.010},
            {"sampleRate": 48000, "channelCount": 6, "outputLatency": 0.012},
        ],
        "high": [
            {"sampleRate": 48000, "channelCount": 6, "outputLatency": 0.010},
            {"sampleRate": 96000, "channelCount": 2, "outputLatency": 0.010},
        ],
    },
    "mac": {
        "low": [
            {"sampleRate": 44100, "channelCount": 2, "outputLatency": 0.013},
            {"sampleRate": 48000, "channelCount": 2, "outputLatency": 0.013},
        ],
        "mid": [
            {"sampleRate": 48000, "channelCount": 2, "outputLatency": 0.012},
            {"sampleRate": 96000, "channelCount": 2, "outputLatency": 0.012},
        ],
        "high": [
            {"sampleRate": 48000, "channelCount": 2, "outputLatency": 0.011},
            {"sampleRate": 96000, "channelCount": 2, "outputLatency": 0.011},
        ],
    },
    "lin": {
        "low": [
            {"sampleRate": 44100, "channelCount": 2, "outputLatency": 0.008},
            {"sampleRate": 48000, "channelCount": 2, "outputLatency": 0.009},
        ],
        "mid": [
            {"sampleRate": 48000, "channelCount": 2, "outputLatency": 0.008},
            {"sampleRate": 96000, "channelCount": 2, "outputLatency": 0.009},
        ],
        "high": [
            {"sampleRate": 48000, "channelCount": 2, "outputLatency": 0.008},
            {"sampleRate": 96000, "channelCount": 2, "outputLatency": 0.008},
        ],
    },
}

_MEDIA_DEVICE_PROFILES = {
    "win": {
        "low": [
            {"microphones": 1, "webcams": 1, "speakers": 1},
            {"microphones": 2, "webcams": 1, "speakers": 2},
        ],
        "mid": [
            {"microphones": 2, "webcams": 1, "speakers": 2},
            {"microphones": 2, "webcams": 2, "speakers": 2},
        ],
        "high": [
            {"microphones": 2, "webcams": 2, "speakers": 2},
            {"microphones": 3, "webcams": 2, "speakers": 3},
        ],
    },
    "mac": {
        "low": [
            {"microphones": 1, "webcams": 1, "speakers": 1},
            {"microphones": 2, "webcams": 1, "speakers": 2},
        ],
        "mid": [
            {"microphones": 2, "webcams": 1, "speakers": 2},
            {"microphones": 2, "webcams": 2, "speakers": 2},
        ],
        "high": [
            {"microphones": 2, "webcams": 2, "speakers": 2},
            {"microphones": 3, "webcams": 2, "speakers": 2},
        ],
    },
    "lin": {
        "low": [
            {"microphones": 1, "webcams": 0, "speakers": 1},
            {"microphones": 1, "webcams": 1, "speakers": 1},
        ],
        "mid": [
            {"microphones": 1, "webcams": 1, "speakers": 2},
            {"microphones": 2, "webcams": 1, "speakers": 2},
        ],
        "high": [
            {"microphones": 2, "webcams": 1, "speakers": 2},
            {"microphones": 2, "webcams": 2, "speakers": 2},
        ],
    },
}

_TASKBAR_HEIGHTS = {
    "win": [40, 48],
    "mac": [25, 37],
    "lin": [0, 27, 36],
}

_MAX_TOUCH_POINTS = {
    "win": {
        "low": [0, 0, 0, 1, 5],
        "mid": [0, 0, 1, 5],
        "high": [0, 1, 5, 10],
    },
    "mac": {
        "low": [0, 0, 0, 1],
        "mid": [0, 0, 1, 5],
        "high": [0, 1, 5],
    },
    "lin": {
        "low": [0, 0, 0, 1],
        "mid": [0, 0, 1],
        "high": [0, 1],
    },
}

_PERFORMANCE_WEIGHTS = {
    "win": ["low", "mid", "mid", "high"],
    "mac": ["low", "mid", "mid", "high"],
    "lin": ["low", "low", "mid", "mid", "high"],
}

PLATFORM_MAP = {
    "win": "Win32",
    "mac": "MacIntel",
    "lin": "Linux x86_64",
}

OSCPU_MAP = {
    "win": "Windows NT 10.0; Win64; x64",
    "mac": "Intel Mac OS X 10.15",
    "lin": "Linux x86_64",
}


def _load_font_map() -> Dict[str, List[str]]:
    with _FONTS_PATH.open("rb") as handle:
        data = orjson.loads(handle.read())
    return {key: list(values) for key, values in data.items()}


OS_FONT_MAP = _load_font_map()


def _choose(generator: Random, values: Sequence[Any]) -> Any:
    return values[generator.randrange(len(values))]


def _screen_distance(candidate: Tuple[int, int, float, int], hint: Tuple[int, int]) -> int:
    return abs(candidate[0] - hint[0]) + abs(candidate[1] - hint[1])


def _infer_gpu_tier(gpu_vendor: str, gpu_renderer: str) -> str:
    token = f"{gpu_vendor} {gpu_renderer}".lower()
    high_markers = (
        "rtx",
        "radeon rx",
        "radeon pro",
        "arc",
        "quadro",
        "apple m",
        "geforce gtx",
        "geforce rtx",
    )
    low_markers = (
        "llvmpipe",
        "swiftshader",
        "software",
        "swrast",
        "uhd graphics 600",
        "hd graphics",
    )
    if any(marker in token for marker in low_markers):
        return "low"
    if any(marker in token for marker in high_markers):
        return "high"
    return "mid"


def _pick_resolution(
    os_family: str,
    performance_tier: str,
    generator: Random,
    screen_hint: Optional[Tuple[int, int]] = None,
) -> Tuple[int, int, float, int]:
    options = _SCREEN_PROFILES[os_family][performance_tier]
    if screen_hint:
        return min(options, key=lambda candidate: _screen_distance(candidate, screen_hint))
    return _choose(generator, options)


def default_fonts(os_family: str) -> List[str]:
    return list(OS_FONT_MAP[os_family])


def build_font_list(
    os_family: str,
    extra_fonts: Optional[Sequence[str]] = None,
    custom_only: bool = False,
) -> List[str]:
    font_list = list(extra_fonts or [])
    if not custom_only:
        font_list = default_fonts(os_family) + font_list

    deduped: List[str] = []
    seen = set()
    for font in font_list:
        if font in seen:
            continue
        deduped.append(font)
        seen.add(font)
    return deduped


@dataclass
class DeviceProfile:
    """
    Coherent device-level state shared by Navigator, Screen, WebGL, and Audio.
    """

    os_family: str
    performance_tier: str = "mid"
    screen_width: int = 1920
    screen_height: int = 1080
    color_depth: int = 24
    device_pixel_ratio: float = 1.0
    taskbar_height: int = 40
    hardware_concurrency: int = 8
    max_touch_points: int = 0
    gpu_vendor: str = ""
    gpu_renderer: str = ""
    webgl_data: Dict[str, Any] = field(default_factory=dict)
    enable_webgl2: bool = False
    fonts: List[str] = field(default_factory=list)
    audio_sample_rate: int = 48000
    audio_channel_count: int = 2
    audio_output_latency: float = 0.01
    microphone_count: int = 1
    webcam_count: int = 1
    speaker_count: int = 1
    platform: str = ""
    oscpu: str = ""

    def to_camoufox_config(self) -> Dict[str, Any]:
        config: Dict[str, Any] = {
            "screen.width": self.screen_width,
            "screen.height": self.screen_height,
            "screen.colorDepth": self.color_depth,
            "screen.pixelDepth": self.color_depth,
            "window.devicePixelRatio": self.device_pixel_ratio,
            "screen.availWidth": self.screen_width,
            "screen.availHeight": max(self.screen_height - self.taskbar_height, 0),
            "screen.availTop": 0,
            "screen.availLeft": 0,
            "navigator.hardwareConcurrency": self.hardware_concurrency,
            "navigator.maxTouchPoints": self.max_touch_points,
            "AudioContext:sampleRate": self.audio_sample_rate,
            "AudioContext:maxChannelCount": self.audio_channel_count,
            "AudioContext:outputLatency": self.audio_output_latency,
            "mediaDevices:enabled": True,
            "mediaDevices:micros": self.microphone_count,
            "mediaDevices:webcams": self.webcam_count,
            "mediaDevices:speakers": self.speaker_count,
            "navigator.platform": self.platform,
            "navigator.oscpu": self.oscpu,
        }

        if self.fonts:
            config["fonts"] = self.fonts
        if self.webgl_data:
            config.update(self.webgl_data)
        if self.gpu_vendor:
            config["webGl:vendor"] = self.gpu_vendor
        if self.gpu_renderer:
            config["webGl:renderer"] = self.gpu_renderer

        return config


def sample_device_profile(
    os_family: str,
    *,
    rng: Optional[Random] = None,
    webgl_enabled: bool = False,
    webgl_config: Optional[Tuple[str, str]] = None,
    screen_hint: Optional[Tuple[int, int]] = None,
) -> DeviceProfile:
    """
    Sample a coherent device profile for the given OS family.

    The sampling order is:
    1. optional WebGL identity
    2. performance tier derived from the GPU family
    3. tier-constrained screen and CPU
    4. OS-specific audio and font bundles
    """
    if os_family not in ("win", "mac", "lin"):
        raise ValueError(f"Invalid OS family: {os_family}. Must be 'win', 'mac', or 'lin'.")

    generator = rng or Random()
    gpu_vendor = ""
    gpu_renderer = ""
    webgl_data: Dict[str, Any] = {}
    enable_webgl2 = False

    if webgl_enabled:
        try:
            from camoufox.webgl.sample import sample_webgl

            if webgl_config:
                webgl_state = sample_webgl(os_family, *webgl_config)
            else:
                webgl_state = sample_webgl(os_family)

            enable_webgl2 = bool(webgl_state.pop("webGl2Enabled", False))
            webgl_data = webgl_state
            gpu_vendor = webgl_state.get("webGl:vendor", "")
            gpu_renderer = webgl_state.get("webGl:renderer", "")
        except Exception:
            webgl_data = {}

    performance_tier = (
        _infer_gpu_tier(gpu_vendor, gpu_renderer)
        if gpu_renderer
        else _choose(generator, _PERFORMANCE_WEIGHTS[os_family])
    )

    last_profile: Optional[DeviceProfile] = None
    for _ in range(6):
        screen_width, screen_height, dpr, color_depth = _pick_resolution(
            os_family,
            performance_tier,
            generator,
            screen_hint=screen_hint,
        )
        audio = _choose(generator, _AUDIO_PROFILES[os_family][performance_tier])
        media_devices = _choose(generator, _MEDIA_DEVICE_PROFILES[os_family][performance_tier])
        last_profile = DeviceProfile(
            os_family=os_family,
            performance_tier=performance_tier,
            screen_width=screen_width,
            screen_height=screen_height,
            color_depth=color_depth,
            device_pixel_ratio=dpr,
            taskbar_height=_choose(generator, _TASKBAR_HEIGHTS[os_family]),
            hardware_concurrency=_choose(generator, _CPU_PROFILES[os_family][performance_tier]),
            max_touch_points=_choose(generator, _MAX_TOUCH_POINTS[os_family][performance_tier]),
            gpu_vendor=gpu_vendor,
            gpu_renderer=gpu_renderer,
            webgl_data=webgl_data,
            enable_webgl2=enable_webgl2,
            fonts=default_fonts(os_family),
            audio_sample_rate=audio["sampleRate"],
            audio_channel_count=audio["channelCount"],
            audio_output_latency=audio["outputLatency"],
            microphone_count=media_devices["microphones"],
            webcam_count=media_devices["webcams"],
            speaker_count=media_devices["speakers"],
            platform=PLATFORM_MAP[os_family],
            oscpu=OSCPU_MAP[os_family],
        )
        if not validate_coherence(last_profile):
            return last_profile

    assert last_profile is not None
    return last_profile
