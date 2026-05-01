/*
Central accessors for session identity state.

The provider groups correlated spoofed values behind one contract so Gecko
patches no longer need to hardcode individual MaskConfig lookups all over the
tree. The backing store is still MaskConfig JSON today, but the interface is
structured so it can later be redirected to shared memory, IPC, or a generated
identity blob without rewriting each patch site.

All Get*State() accessors cache their result via std::call_once so the JSON
is parsed at most once per subsystem per process lifetime.
*/

#pragma once

#include "MaskConfig.hpp"
#include <array>
#include <cstdint>
#include <mutex>
#include <optional>
#include <string>
#include <tuple>
#include <vector>

namespace IdentityStateProvider {

// ── Subsystem state structs ─────────────────────────────────────────

struct NavigatorState {
  std::optional<std::string> userAgent;
  std::optional<std::string> appCodeName;
  std::optional<std::string> appName;
  std::optional<std::string> appVersion;
  std::optional<std::string> buildID;
  std::optional<std::string> language;
  std::vector<std::string> languages;
  std::optional<std::string> platform;
  std::optional<std::string> oscpu;
  std::optional<std::string> product;
  std::optional<std::string> productSub;
  std::optional<std::string> doNotTrack;
  std::optional<bool> globalPrivacyControl;
  std::optional<uint64_t> hardwareConcurrency;
  std::optional<uint32_t> maxTouchPoints;
};

struct DisplayState {
  std::optional<uint32_t> availLeft;
  std::optional<uint32_t> availTop;
  std::optional<int32_t> screenX;
  std::optional<int32_t> screenY;
  std::optional<uint32_t> width;
  std::optional<uint32_t> height;
  std::optional<uint32_t> availWidth;
  std::optional<uint32_t> availHeight;
  std::optional<uint32_t> outerWidth;
  std::optional<uint32_t> outerHeight;
  std::optional<uint32_t> innerWidth;
  std::optional<uint32_t> innerHeight;
  std::optional<int32_t> clientLeft;
  std::optional<int32_t> clientTop;
  std::optional<uint32_t> clientWidth;
  std::optional<uint32_t> clientHeight;
  std::optional<uint32_t> historyLength;
  std::optional<uint32_t> pixelDepth;
  std::optional<uint32_t> colorDepth;
  std::optional<double> devicePixelRatio;
  std::optional<int32_t> scrollMinX;
  std::optional<int32_t> scrollMinY;
  std::optional<int32_t> scrollMaxX;
  std::optional<int32_t> scrollMaxY;
  std::optional<double> pageXOffset;
  std::optional<double> pageYOffset;
};

struct AudioState {
  std::optional<uint32_t> sampleRate;
  std::optional<uint32_t> maxChannelCount;
  std::optional<double> outputLatency;
};

struct WebGLState {
  std::optional<std::string> vendor;
  std::optional<std::string> renderer;
  // Extended WebGL state for cross-subsystem coherence validation.
  // MAX_VIEWPORT_DIMS should never exceed the display resolution.
  std::optional<uint32_t> maxViewportWidth;
  std::optional<uint32_t> maxViewportHeight;
  bool webGl2Enabled = false;
};

struct CanvasState {
  std::optional<int32_t> aaOffset;
  std::optional<bool> aaCapOffset;
  std::optional<uint64_t> noiseSeed;
};

struct FontState {
  std::vector<std::string> fontList;
  std::optional<uint32_t> spacingSeed;
};

struct HeaderState {
  std::optional<std::string> userAgent;
  std::optional<std::string> acceptLanguage;
  std::optional<std::string> acceptEncoding;
};

struct BatteryState {
  std::optional<bool> charging;
  std::optional<double> chargingTime;
  std::optional<double> dischargingTime;
  std::optional<double> level;
};

struct MediaDeviceState {
  bool enabled = false;
  uint32_t microphones = 3;
  uint32_t webcams = 1;
  uint32_t speakers = 1;
};

struct VoiceState {
  std::optional<bool> blockIfNotDefined;
  std::optional<bool> fakeCompletion;
  std::optional<double> charsPerSecond;
  std::vector<std::tuple<std::string, std::string, std::string, bool, bool>>
      voices;
};

struct RuntimeState {
  std::optional<bool> enableRemoteSubframes;
  std::optional<bool> disableTheming;
};

// ── Aggregated identity blob ────────────────────────────────────────

struct IdentityBlob {
  std::optional<NavigatorState> navigator;
  std::optional<DisplayState> display;
  std::optional<AudioState> audio;
  std::optional<WebGLState> webgl;
  std::optional<CanvasState> canvas;
  std::optional<FontState> fonts;
  std::optional<HeaderState> headers;
  std::optional<BatteryState> battery;
  std::optional<MediaDeviceState> mediaDevices;
  std::optional<VoiceState> voice;
  std::optional<RuntimeState> runtime;
};

// ── Cached accessors ────────────────────────────────────────────────
// Each accessor parses the MaskConfig JSON at most once and caches the
// result for the process lifetime.

namespace detail {

template <typename T>
struct CachedState {
  std::once_flag flag;
  std::optional<T> value;
};

}  // namespace detail

inline std::optional<NavigatorState> GetNavigatorState() {
  static detail::CachedState<NavigatorState> cache;
  std::call_once(cache.flag, []() {
    NavigatorState state{
        MaskConfig::GetString("navigator.userAgent"),
        MaskConfig::GetString("navigator.appCodeName"),
        MaskConfig::GetString("navigator.appName"),
        MaskConfig::GetString("navigator.appVersion"),
        MaskConfig::GetString("navigator.buildID"),
        MaskConfig::GetString("navigator.language"),
        MaskConfig::GetStringList("navigator.languages"),
        MaskConfig::GetString("navigator.platform"),
        MaskConfig::GetString("navigator.oscpu"),
        MaskConfig::GetString("navigator.product"),
        MaskConfig::GetString("navigator.productSub"),
        MaskConfig::GetString("navigator.doNotTrack"),
        MaskConfig::GetBool("navigator.globalPrivacyControl"),
        MaskConfig::GetUint64("navigator.hardwareConcurrency"),
        MaskConfig::GetUint32("navigator.maxTouchPoints"),
    };

    if (!state.userAgent && !state.appCodeName && !state.appName &&
        !state.appVersion && !state.buildID && !state.language &&
        state.languages.empty() && !state.platform && !state.oscpu &&
        !state.product && !state.productSub && !state.doNotTrack &&
        !state.globalPrivacyControl && !state.hardwareConcurrency &&
        !state.maxTouchPoints) {
      cache.value = std::nullopt;
    } else {
      cache.value = state;
    }
  });
  return cache.value;
}

inline std::optional<DisplayState> GetDisplayState() {
  static detail::CachedState<DisplayState> cache;
  std::call_once(cache.flag, []() {
    DisplayState state{
        MaskConfig::GetUint32("screen.availLeft"),
        MaskConfig::GetUint32("screen.availTop"),
        MaskConfig::GetInt32("window.screenX"),
        MaskConfig::GetInt32("window.screenY"),
        MaskConfig::GetUint32("screen.width"),
        MaskConfig::GetUint32("screen.height"),
        MaskConfig::GetUint32("screen.availWidth"),
        MaskConfig::GetUint32("screen.availHeight"),
        MaskConfig::GetUint32("window.outerWidth"),
        MaskConfig::GetUint32("window.outerHeight"),
        MaskConfig::GetUint32("window.innerWidth"),
        MaskConfig::GetUint32("window.innerHeight"),
        MaskConfig::GetInt32("document.body.clientLeft"),
        MaskConfig::GetInt32("document.body.clientTop"),
        MaskConfig::GetUint32("document.body.clientWidth"),
        MaskConfig::GetUint32("document.body.clientHeight"),
        MaskConfig::GetUint32("window.history.length"),
        MaskConfig::GetUint32("screen.pixelDepth"),
        MaskConfig::GetUint32("screen.colorDepth"),
        MaskConfig::GetDouble("window.devicePixelRatio"),
        MaskConfig::GetInt32("window.scrollMinX"),
        MaskConfig::GetInt32("window.scrollMinY"),
        MaskConfig::GetInt32("window.scrollMaxX"),
        MaskConfig::GetInt32("window.scrollMaxY"),
        MaskConfig::GetDouble("screen.pageXOffset"),
        MaskConfig::GetDouble("screen.pageYOffset"),
    };

    if (!state.availLeft && !state.availTop && !state.screenX &&
        !state.screenY && !state.width && !state.height && !state.availWidth &&
        !state.availHeight && !state.outerWidth &&
        !state.outerHeight && !state.innerWidth && !state.innerHeight &&
        !state.clientLeft && !state.clientTop && !state.clientWidth &&
        !state.clientHeight && !state.historyLength && !state.pixelDepth &&
        !state.colorDepth && !state.devicePixelRatio && !state.scrollMinX &&
        !state.scrollMinY && !state.scrollMaxX && !state.scrollMaxY &&
        !state.pageXOffset && !state.pageYOffset) {
      cache.value = std::nullopt;
    } else {
      cache.value = state;
    }
  });
  return cache.value;
}

inline std::optional<AudioState> GetAudioState() {
  static detail::CachedState<AudioState> cache;
  std::call_once(cache.flag, []() {
    AudioState state{
        MaskConfig::GetUint32("AudioContext:sampleRate"),
        MaskConfig::GetUint32("AudioContext:maxChannelCount"),
        MaskConfig::GetDouble("AudioContext:outputLatency"),
    };
    if (!state.sampleRate && !state.maxChannelCount && !state.outputLatency) {
      cache.value = std::nullopt;
    } else {
      cache.value = state;
    }
  });
  return cache.value;
}

inline std::optional<WebGLState> GetWebGLState() {
  static detail::CachedState<WebGLState> cache;
  std::call_once(cache.flag, []() {
    WebGLState state{
        MaskConfig::GetString("webGl:vendor"),
        MaskConfig::GetString("webGl:renderer"),
        std::nullopt,  // maxViewportWidth — derived from display if needed
        std::nullopt,  // maxViewportHeight
        false,         // webGl2Enabled
    };

    // Cross-reference with display state: MAX_VIEWPORT_DIMS must be >= screen
    auto displayWidth = MaskConfig::GetUint32("screen.width");
    auto displayHeight = MaskConfig::GetUint32("screen.height");
    if (displayWidth) state.maxViewportWidth = displayWidth;
    if (displayHeight) state.maxViewportHeight = displayHeight;

    if (!state.vendor && !state.renderer) {
      cache.value = std::nullopt;
    } else {
      cache.value = state;
    }
  });
  return cache.value;
}

inline std::optional<CanvasState> GetCanvasState() {
  static detail::CachedState<CanvasState> cache;
  std::call_once(cache.flag, []() {
    CanvasState state{
        MaskConfig::GetInt32("canvas:aaOffset"),
        MaskConfig::GetBool("canvas:aaCapOffset"),
        MaskConfig::GetUint64("canvas:noiseSeed"),
    };
    if (!state.aaOffset && !state.aaCapOffset && !state.noiseSeed) {
      cache.value = std::nullopt;
    } else {
      cache.value = state;
    }
  });
  return cache.value;
}

inline std::optional<FontState> GetFontState() {
  static detail::CachedState<FontState> cache;
  std::call_once(cache.flag, []() {
    FontState state{
        MaskConfig::GetStringList("fonts"),
        MaskConfig::GetUint32("fonts:spacing_seed"),
    };
    if (state.fontList.empty() && !state.spacingSeed) {
      cache.value = std::nullopt;
    } else {
      cache.value = state;
    }
  });
  return cache.value;
}

inline std::optional<HeaderState> GetHeaderState() {
  static detail::CachedState<HeaderState> cache;
  std::call_once(cache.flag, []() {
    HeaderState state{
        MaskConfig::GetString("headers.User-Agent"),
        MaskConfig::GetString("headers.Accept-Language"),
        MaskConfig::GetString("headers.Accept-Encoding"),
    };
    if (!state.userAgent && !state.acceptLanguage && !state.acceptEncoding) {
      cache.value = std::nullopt;
    } else {
      cache.value = state;
    }
  });
  return cache.value;
}

inline std::optional<BatteryState> GetBatteryState() {
  static detail::CachedState<BatteryState> cache;
  std::call_once(cache.flag, []() {
    BatteryState state{
        MaskConfig::GetBool("battery:charging"),
        MaskConfig::GetDouble("battery:chargingTime"),
        MaskConfig::GetDouble("battery:dischargingTime"),
        MaskConfig::GetDouble("battery:level"),
    };
    if (!state.charging && !state.chargingTime && !state.dischargingTime &&
        !state.level) {
      cache.value = std::nullopt;
    } else {
      cache.value = state;
    }
  });
  return cache.value;
}

inline std::optional<MediaDeviceState> GetMediaDeviceState() {
  static detail::CachedState<MediaDeviceState> cache;
  std::call_once(cache.flag, []() {
    if (!MaskConfig::GetBool("mediaDevices:enabled").has_value() &&
        !MaskConfig::GetUint32("mediaDevices:micros").has_value() &&
        !MaskConfig::GetUint32("mediaDevices:webcams").has_value() &&
        !MaskConfig::GetUint32("mediaDevices:speakers").has_value()) {
      cache.value = std::nullopt;
    } else {
      cache.value = MediaDeviceState{
          MaskConfig::GetBool("mediaDevices:enabled").value_or(false),
          MaskConfig::GetUint32("mediaDevices:micros").value_or(3),
          MaskConfig::GetUint32("mediaDevices:webcams").value_or(1),
          MaskConfig::GetUint32("mediaDevices:speakers").value_or(1),
      };
    }
  });
  return cache.value;
}

inline std::optional<VoiceState> GetVoiceState() {
  static detail::CachedState<VoiceState> cache;
  std::call_once(cache.flag, []() {
    VoiceState state{
        MaskConfig::GetBool("voices:blockIfNotDefined"),
        MaskConfig::GetBool("voices:fakeCompletion"),
        MaskConfig::GetDouble("voices:fakeCompletion:charsPerSecond"),
        MaskConfig::MVoices().value_or(
            std::vector<
                std::tuple<std::string, std::string, std::string, bool, bool>>{}),
    };
    if (!state.blockIfNotDefined && !state.fakeCompletion &&
        !state.charsPerSecond && state.voices.empty()) {
      cache.value = std::nullopt;
    } else {
      cache.value = state;
    }
  });
  return cache.value;
}

inline std::optional<RuntimeState> GetRuntimeState() {
  static detail::CachedState<RuntimeState> cache;
  std::call_once(cache.flag, []() {
    RuntimeState state{
        MaskConfig::GetBool("enableRemoteSubframes"),
        MaskConfig::GetBool("disableTheming"),
    };
    if (!state.enableRemoteSubframes && !state.disableTheming) {
      cache.value = std::nullopt;
    } else {
      cache.value = state;
    }
  });
  return cache.value;
}

// ── Aggregated blob accessor ────────────────────────────────────────

inline const IdentityBlob& GetIdentityBlob() {
  static detail::CachedState<IdentityBlob> cache;
  static IdentityBlob blob;
  std::call_once(cache.flag, []() {
    blob.navigator = GetNavigatorState();
    blob.display = GetDisplayState();
    blob.audio = GetAudioState();
    blob.webgl = GetWebGLState();
    blob.canvas = GetCanvasState();
    blob.fonts = GetFontState();
    blob.headers = GetHeaderState();
    blob.battery = GetBatteryState();
    blob.mediaDevices = GetMediaDeviceState();
    blob.voice = GetVoiceState();
    blob.runtime = GetRuntimeState();
  });
  return blob;
}

}  // namespace IdentityStateProvider
