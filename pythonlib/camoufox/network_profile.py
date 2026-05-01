import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from ua_parser import user_agent_parser

TransportMode = Literal["firefox-native", "utls-sidecar"]


@dataclass
class NetworkProfile:
    """
    Independent network egress identity.
    Defines the TLS and HTTP/2 fingerprint representation.
    """
    browser_family: str
    major_version: int
    tls_profile_id: str
    client_hello_template: Dict[str, Any]
    http2_template: Dict[str, Any]
    alpn_policy: List[str]
    proxy_egress_class: str  # "nss" or "utls-sidecar"
    transport_mode: TransportMode = "firefox-native"
    grease_enabled: bool = False
    ja4_family: Optional[str] = None
    supports_nss_env_overrides: bool = False
    sidecar_template: Dict[str, Any] = field(default_factory=dict)

    # NSS override fields for direct cipher/extension control
    nss_cipher_overrides: Optional[List[int]] = None
    nss_extension_overrides: Optional[List[int]] = None
    nss_named_group_overrides: Optional[List[int]] = None
    nss_sigalg_overrides: Optional[List[int]] = None

    def is_nss_native(self) -> bool:
        return self.transport_mode == "firefox-native"

    def requires_sidecar(self) -> bool:
        return self.transport_mode == "utls-sidecar"

    def validate_browser_family(self, browser_family: str) -> bool:
        return browser_family.lower() == self.browser_family.lower()

    def validate_browser_major(self, major_version: int) -> bool:
        return major_version == self.major_version

    def validate_against_ua(self, user_agent: str) -> List[str]:
        """
        Cross-check a User-Agent string against this network profile.

        Returns a list of issue descriptions. Empty list = valid match.
        """
        issues: List[str] = []
        parsed = user_agent_parser.ParseUserAgent(user_agent)
        ua_family = (parsed.get("family") or "").lower()
        try:
            ua_major = int(parsed.get("major", "0"))
        except (ValueError, TypeError):
            ua_major = 0

        # Family mismatch: Firefox UA should never use a Chrome TLS profile
        if self.browser_family == "firefox" and "firefox" not in ua_family:
            issues.append(
                f"TLS profile is firefox but UA family is '{ua_family}'"
            )
        elif self.browser_family == "chrome" and "chrome" not in ua_family:
            issues.append(
                f"TLS profile is chrome but UA family is '{ua_family}'"
            )

        # Major version mismatch (tolerate ±2 for minor version skew)
        if ua_major > 0 and abs(ua_major - self.major_version) > 2:
            issues.append(
                f"TLS profile version {self.major_version} does not match "
                f"UA major version {ua_major} (drift > 2)"
            )

        # GREASE check: Firefox never uses GREASE
        if self.browser_family == "firefox" and self.grease_enabled:
            issues.append(
                "GREASE is enabled but Firefox does not use GREASE extensions"
            )

        return issues

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "browser_family": self.browser_family,
            "major_version": self.major_version,
            "tls_profile_id": self.tls_profile_id,
            "proxy_egress_class": self.proxy_egress_class,
            "transport_mode": self.transport_mode,
            "grease_enabled": self.grease_enabled,
            "ja4_family": self.ja4_family,
            "alpn_policy": list(self.alpn_policy),
            "sidecar_template": dict(self.sidecar_template),
        }

    def to_env_metadata(self) -> str:
        return json.dumps(self.to_metadata(), sort_keys=True, separators=(",", ":"))

    def to_sidecar_env(self) -> Dict[str, str]:
        """
        Generate environment variables for the Go uTLS sidecar proxy.
        """
        env: Dict[str, str] = {
            "CAMOU_UTLS_PROFILE": self.tls_profile_id,
        }
        if self.sidecar_template:
            env["CAMOU_UTLS_TEMPLATE"] = json.dumps(
                self.sidecar_template, sort_keys=True, separators=(",", ":")
            )
        # Always include the full identity JSON for custom ClientHelloSpec
        env["CAMOU_UTLS_IDENTITY_JSON"] = self.to_sidecar_identity_json()
        return env

    def to_sidecar_identity_json(self) -> str:
        """
        Serialize the full TLS + HTTP/2 profile as a JSON blob for the
        Go uTLS sidecar's buildCustomSpec() function.

        This enables the sidecar to construct a pixel-perfect ClientHelloSpec
        from the IdentityCoherenceEngine's selected identity, rather than
        relying on pre-built utls.HelloFirefox_120 profiles.
        """
        tls_data: Dict[str, Any] = {}
        http2_data: Dict[str, Any] = {}

        # TLS cipher suite codes
        if self.nss_cipher_overrides:
            tls_data["cipherSuiteCodes"] = list(self.nss_cipher_overrides)

        # TLS extension codes (for reference — sidecar builds extensions structurally)
        if self.nss_extension_overrides:
            tls_data["extensionCodes"] = list(self.nss_extension_overrides)

        # Named groups
        if self.nss_named_group_overrides:
            tls_data["namedGroupCodes"] = list(self.nss_named_group_overrides)

        # Signature algorithms
        if self.nss_sigalg_overrides:
            tls_data["sigAlgCodes"] = list(self.nss_sigalg_overrides)

        # ALPN
        tls_data["alpn"] = list(self.alpn_policy)

        # HTTP/2 SETTINGS (from the profile's http2_template)
        if self.http2_template:
            http2_data = {
                "headerTableSize": self.http2_template.get("http2:headerTableSize", 65536),
                "enablePush": self.http2_template.get("http2:enablePush", 0),
                "initialWindowSize": self.http2_template.get("http2:initialWindowSize", 131072),
                "maxFrameSize": self.http2_template.get("http2:maxFrameSize", 16384),
                "windowUpdate": self.http2_template.get("http2:windowUpdate", 12517377),
            }

        blob = {"tls": tls_data, "http2": http2_data}
        return json.dumps(blob, sort_keys=True, separators=(",", ":"))

