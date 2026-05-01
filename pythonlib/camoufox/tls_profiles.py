"""
TLS and HTTP/2 fingerprint profiles for Firefox versions.

These profiles model the intended network identity for a session.
Camoufox is Firefox-only at the engine level, so the supported production
path is Firefox-version-correct fingerprints rather than cross-engine
impersonation. Sidecar egress is modeled explicitly for future use instead
of silently pretending NSS can emit non-Firefox handshakes.

Reference data sourced from:
- curl-impersonate (github.com/lwthiker/curl-impersonate)
- browserleaks.com/ssl captures
- tls.browserleaks.com/json raw data
- FoxIO-LLC/ja4 technical specifications
"""

from copy import deepcopy
from typing import Any, Dict, List, Optional
from ua_parser import user_agent_parser
from .network_profile import NetworkProfile


# ── Firefox 135 TLS Profile ──────────────────────────────────────────
# Captured from a clean Firefox 135.0.1 installation.

FIREFOX_135_TLS = {
    # TLS 1.3 cipher suites (these are always first)
    "tls:cipherSuites": [
        # TLS 1.3
        "TLS_AES_128_GCM_SHA256",           # 0x1301
        "TLS_CHACHA20_POLY1305_SHA256",      # 0x1303
        "TLS_AES_256_GCM_SHA384",            # 0x1302
        # TLS 1.2
        "TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256",    # 0xc02b
        "TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256",      # 0xc02f
        "TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256",  # 0xcca9
        "TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256",    # 0xcca8
        "TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384",    # 0xc02c
        "TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384",      # 0xc030
        "TLS_ECDHE_ECDSA_WITH_AES_256_CBC_SHA",        # 0xc00a  (legacy)
        "TLS_ECDHE_ECDSA_WITH_AES_128_CBC_SHA",        # 0xc009  (legacy)
        "TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA",          # 0xc013  (legacy)
        "TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA",          # 0xc014  (legacy)
        "TLS_RSA_WITH_AES_128_GCM_SHA256",             # 0x009c
        "TLS_RSA_WITH_AES_256_GCM_SHA384",             # 0x009d
        "TLS_RSA_WITH_AES_128_CBC_SHA",                 # 0x002f  (legacy)
        "TLS_RSA_WITH_AES_256_CBC_SHA",                 # 0x0035  (legacy)
    ],

    # Cipher suite codes (numeric, for direct NSS consumption)
    "tls:cipherSuiteCodes": [
        0x1301, 0x1303, 0x1302,
        0xc02b, 0xc02f, 0xcca9, 0xcca8,
        0xc02c, 0xc030,
        0xc00a, 0xc009, 0xc013, 0xc014,
        0x009c, 0x009d, 0x002f, 0x0035,
    ],

    # TLS extension ordering (Firefox 135 specific)
    "tls:extensions": [
        "server_name",                          # 0
        "extended_master_secret",               # 23
        "renegotiation_info",                   # 65281
        "supported_groups",                     # 10
        "ec_point_formats",                     # 11
        "session_ticket",                       # 35
        "application_layer_protocol_negotiation",  # 16 (ALPN)
        "status_request",                       # 5
        "delegated_credentials",                # 34
        "key_share",                            # 51
        "supported_versions",                   # 43
        "signature_algorithms",                 # 13
        "psk_key_exchange_modes",               # 45
        "record_size_limit",                    # 28
        "padding",                              # 21
    ],

    # Extension type codes (numeric)
    "tls:extensionCodes": [
        0, 23, 65281, 10, 11, 35, 16, 5, 34, 51, 43, 13, 45, 28, 21,
    ],

    # Named groups / curves (ordered)
    "tls:namedGroups": [
        "x25519",           # 0x001d
        "secp256r1",        # 0x0017
        "secp384r1",        # 0x0018
        "secp521r1",        # 0x0019
        "ffdhe2048",        # 0x0100
        "ffdhe3072",        # 0x0101
    ],

    "tls:namedGroupCodes": [
        0x001d, 0x0017, 0x0018, 0x0019, 0x0100, 0x0101,
    ],

    # Signature algorithms (ordered)
    "tls:signatureAlgorithms": [
        "ecdsa_secp256r1_sha256",    # 0x0403
        "ecdsa_secp384r1_sha384",    # 0x0503
        "ecdsa_secp521r1_sha512",    # 0x0603
        "rsa_pss_rsae_sha256",       # 0x0804
        "rsa_pss_rsae_sha384",       # 0x0805
        "rsa_pss_rsae_sha512",       # 0x0806
        "rsa_pkcs1_sha256",          # 0x0401
        "rsa_pkcs1_sha384",          # 0x0501
        "rsa_pkcs1_sha512",          # 0x0601
        "ecdsa_sha1",                # 0x0203
        "rsa_pkcs1_sha1",            # 0x0201
    ],

    "tls:signatureAlgorithmCodes": [
        0x0403, 0x0503, 0x0603,
        0x0804, 0x0805, 0x0806,
        0x0401, 0x0501, 0x0601,
        0x0203, 0x0201,
    ],

    # ALPN protocols
    "tls:alpn": ["h2", "http/1.1"],

    # TLS versions supported
    "tls:supportedVersions": [0x0304, 0x0303],  # TLS 1.3, TLS 1.2
}


# ── Firefox 135 HTTP/2 SETTINGS Profile ──────────────────────────────
# Captured from Firefox 135's Http2Session::SendHello()

FIREFOX_135_HTTP2 = {
    # SETTINGS frame values
    "http2:headerTableSize": 65536,       # SETTINGS_HEADER_TABLE_SIZE (0x1)
    "http2:enablePush": 0,                # SETTINGS_ENABLE_PUSH (0x2) — Firefox disables
    "http2:initialWindowSize": 131072,    # SETTINGS_INITIAL_WINDOW_SIZE (0x4) — 128KB
    "http2:maxFrameSize": 16384,          # SETTINGS_MAX_FRAME_SIZE (0x5) — 16KB

    # Initial WINDOW_UPDATE increment (connection-level)
    "http2:windowUpdate": 12517377,       # Firefox's specific value

    # Priority/weight settings
    "http2:priorityWeight": 42,           # Default stream weight
}


# ── Profile Registry ─────────────────────────────────────────────────

def _build_firefox_profile(major_version: int) -> NetworkProfile:
    return NetworkProfile(
        browser_family="firefox",
        major_version=major_version,
        tls_profile_id=f"firefox{major_version}",
        client_hello_template=deepcopy(FIREFOX_135_TLS),
        http2_template=deepcopy(FIREFOX_135_HTTP2),
        alpn_policy=list(FIREFOX_135_TLS["tls:alpn"]),
        proxy_egress_class="nss",
        transport_mode="firefox-native",
        grease_enabled=False,
        ja4_family="firefox",
        supports_nss_env_overrides=True,
        nss_cipher_overrides=list(FIREFOX_135_TLS["tls:cipherSuiteCodes"]),
        nss_extension_overrides=list(FIREFOX_135_TLS["tls:extensionCodes"]),
        nss_named_group_overrides=list(FIREFOX_135_TLS["tls:namedGroupCodes"]),
        nss_sigalg_overrides=list(FIREFOX_135_TLS["tls:signatureAlgorithmCodes"]),
    )


TLS_PROFILES: Dict[str, NetworkProfile] = {
    f"firefox{major_version}": _build_firefox_profile(major_version)
    for major_version in (133, 134, 135)
}


def get_tls_profile(browser_family: str, major_version: int) -> Optional[NetworkProfile]:
    """
    Get the TLS/HTTP/2 profile for a specific browser family and version.
    
    Args:
        browser_family: 'firefox' or 'chrome'
        major_version: Major version number (e.g. 135)

    Returns:
        NetworkProfile or None if the version is not known.
    """
    return TLS_PROFILES.get(f"{browser_family}{major_version}")


def get_tls_profile_strict(browser_family: str, major_version: int) -> NetworkProfile:
    """
    Like get_tls_profile, but raises ValueError if the profile is not found.
    """
    profile = get_tls_profile(browser_family, major_version)
    if profile is None:
        available = sorted(TLS_PROFILES.keys())
        raise ValueError(
            f"No TLS profile for {browser_family}{major_version}. "
            f"Available: {', '.join(available)}"
        )
    return profile


def get_tls_env_vars(profile: NetworkProfile) -> Dict[str, str]:
    """
    Convert a TLS profile into environment variables for NSS.

    NSS reads these before Gecko/XPCOM initializes, so they must
    be set as process environment variables (not MaskConfig).

    Returns:
        Dict of CAMOU_TLS_* environment variable key-value pairs.
    """
    env_vars: Dict[str, str] = {
        "CAMOU_NET_PROFILE": profile.to_env_metadata(),
    }
    if not profile.is_nss_native() or not profile.supports_nss_env_overrides:
        return env_vars

    template = profile.client_hello_template

    # Cipher suite codes as comma-separated hex
    if "tls:cipherSuiteCodes" in template:
        codes = template["tls:cipherSuiteCodes"]
        env_vars["CAMOU_TLS_CIPHERS"] = ",".join(f"0x{c:04x}" for c in codes)

    # Extension type codes as comma-separated hex
    if "tls:extensionCodes" in template:
        codes = template["tls:extensionCodes"]
        env_vars["CAMOU_TLS_EXTENSIONS"] = ",".join(f"0x{c:04x}" for c in codes)

    # Named group codes
    if "tls:namedGroupCodes" in template:
        codes = template["tls:namedGroupCodes"]
        env_vars["CAMOU_TLS_GROUPS"] = ",".join(f"0x{c:04x}" for c in codes)

    # Signature algorithm codes
    if "tls:signatureAlgorithmCodes" in template:
        codes = template["tls:signatureAlgorithmCodes"]
        env_vars["CAMOU_TLS_SIGALGS"] = ",".join(f"0x{c:04x}" for c in codes)

    # ALPN
    if "tls:alpn" in template:
        env_vars["CAMOU_TLS_ALPN"] = ",".join(template["tls:alpn"])

    return env_vars


def get_http2_config(profile: NetworkProfile) -> Dict[str, Any]:
    """
    Extract HTTP/2 SETTINGS values for MaskConfig injection.

    These go through the standard CAMOU_CONFIG_* path because
    Http2Session.cpp can read MaskConfig at runtime.
    """
    if not profile.is_nss_native():
        return {} # Let the sidecar proxy handle HTTP/2 settings

    http2_keys = [
        "http2:headerTableSize",
        "http2:enablePush",
        "http2:initialWindowSize",
        "http2:maxFrameSize",
        "http2:windowUpdate",
        "http2:priorityWeight",
    ]
    template = profile.http2_template
    return {k: template[k] for k in http2_keys if k in template}


# ── TLS Profile Validator ────────────────────────────────────────────

class TLSProfileValidator:
    """
    Cross-validates a TLS/HTTP/2 profile against a browser identity.

    Rejects configurations that would create detectable mismatches:
    - Firefox UA with Chrome TLS fingerprint
    - Cipher ordering that doesn't match the browser convention
    - Extension ordering inconsistencies
    """

    # Firefox-specific: TLS 1.3 cipher suites must always come first
    _FIREFOX_TLS13_PREFIXES = {0x1301, 0x1303, 0x1302}

    # Firefox never sends GREASE values
    _GREASE_VALUES = {
        0x0a0a, 0x1a1a, 0x2a2a, 0x3a3a, 0x4a4a,
        0x5a5a, 0x6a6a, 0x7a7a, 0x8a8a, 0x9a9a,
        0xaaaa, 0xbaba, 0xcaca, 0xdada, 0xeaea, 0xfafa,
    }

    def validate_ua_match(
        self,
        ua_string: str,
        profile: NetworkProfile,
    ) -> List[str]:
        """
        Validate that the User-Agent matches the TLS profile's browser family/version.
        """
        return profile.validate_against_ua(ua_string)

    def validate_cipher_order(self, profile: NetworkProfile) -> List[str]:
        """
        Validate that cipher suite ordering matches browser conventions.
        """
        issues: List[str] = []
        codes = profile.client_hello_template.get("tls:cipherSuiteCodes", [])
        if not codes:
            return issues

        if profile.browser_family == "firefox":
            # Firefox: TLS 1.3 suites must come first
            tls13_end = 0
            for i, code in enumerate(codes):
                if code in self._FIREFOX_TLS13_PREFIXES:
                    tls13_end = i + 1
                elif tls13_end > 0:
                    break

            for i in range(tls13_end, len(codes)):
                if codes[i] in self._FIREFOX_TLS13_PREFIXES:
                    issues.append(
                        f"TLS 1.3 cipher 0x{codes[i]:04x} appears after TLS 1.2 "
                        f"ciphers at position {i} — Firefox always sends TLS 1.3 first"
                    )

            # Firefox never sends GREASE cipher suites
            for code in codes:
                if code in self._GREASE_VALUES:
                    issues.append(
                        f"GREASE cipher 0x{code:04x} found — Firefox does not "
                        f"use GREASE in cipher suites"
                    )

        return issues

    def validate_extension_order(self, profile: NetworkProfile) -> List[str]:
        """
        Validate that TLS extension ordering is consistent with browser conventions.
        """
        issues: List[str] = []
        codes = profile.client_hello_template.get("tls:extensionCodes", [])
        if not codes:
            return issues

        if profile.browser_family == "firefox":
            # Firefox: server_name (0) is always the first extension
            if codes and codes[0] != 0:
                issues.append(
                    f"First extension is 0x{codes[0]:04x} — Firefox always "
                    f"sends server_name (0x0000) first"
                )

            # Firefox: GREASE should not appear in extensions
            for code in codes:
                if code in self._GREASE_VALUES:
                    issues.append(
                        f"GREASE extension 0x{code:04x} found — Firefox does "
                        f"not use GREASE extensions"
                    )

            # Firefox: supported_versions (43) should appear after key_share (51)
            try:
                key_share_idx = codes.index(51)
                supported_versions_idx = codes.index(43)
                if supported_versions_idx < key_share_idx:
                    issues.append(
                        "supported_versions (43) appears before key_share (51) "
                        "— Firefox sends key_share first"
                    )
            except ValueError:
                pass  # Extension not present — not an ordering issue

        return issues

    def validate_all(
        self,
        ua_string: str,
        profile: NetworkProfile,
    ) -> List[str]:
        """Run all validation checks."""
        issues: List[str] = []
        issues.extend(self.validate_ua_match(ua_string, profile))
        issues.extend(self.validate_cipher_order(profile))
        issues.extend(self.validate_extension_order(profile))
        return issues
