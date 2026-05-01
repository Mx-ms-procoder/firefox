"""
CAPTCHA Master-Scanner v8  –  TikTok drei separate Provider
══════════════════════════════════════════════════════════════════════════
Änderungen gegenüber v7:
  • TikTok vollständig refaktoriert:
      – Ein gemeinsamer TikTokProvider → drei separate Provider-Klassen:
          TikTokRotateProvider      (Rotate CAPTCHA)
          TikTokSlideProvider       (Puzzle Slide CAPTCHA)
          TikTok3DObjectsProvider   (3D Objects / Shapes CAPTCHA)
      – Gemeinsame Hilfsfunktion _tikTokContainerPresent() vermeidet
        redundante DOM-Traversal in allen drei Providern
      – Jeder Provider enthält präzise Negativfilter gegen die anderen
        zwei Typen (Slider-Check, Rotate-Bild-Check)
      – V1 + V2 Selektoren je Typ vollständig dokumentiert
      – Detaillierte description mit Selektoren + Lösungshinweisen
  • Orchestrator: drei TikTok-Provider in Priorität Rotate > Slide > 3D,
    hasTikTok-Flag verhindert Mehrfachreportings
  • specificTypes um alle drei TikTok-Namen erweitert

Playwright-Nutzung (Camoufox):
    import asyncio, master_scanner
    from playwright.async_api import async_playwright
    from camoufox.async_api import AsyncNewBrowser

    async def main():
        async with async_playwright() as pw:
            browser = await AsyncNewBrowser(pw, headless=False)
            page    = await browser.new_page()
            await page.goto("https://example.com")

            # Einmaliger Scan:
            results = await master_scanner.scan_page(page)

            # Dauerhafter Hintergrund-Scan (kompletter Context):
            asyncio.create_task(master_scanner.live_scan(browser.contexts[0]))

    asyncio.run(main())

Legacy-CDP (run_stealth_test.py, threading):
    threading.Thread(
        target=master_scanner.live_scan_cdp,
        args=(browser_config.CDP_HOST, browser_config.CDP_PORT),
        daemon=True,
    ).start()

Benötigt:  pip install playwright camoufox
"""

from __future__ import annotations

import sys
import os
import time
import json
import asyncio
import urllib.request
from datetime import datetime
from typing import Any, Callable, Optional
from weakref import WeakKeyDictionary

# ══════════════════════════════════════════════════════════════════
#  KONFIGURATION
# ══════════════════════════════════════════════════════════════════

CDP_PORT         = int(os.environ.get("CDP_PORT", 9222))
CDP_HOST         = os.environ.get("CDP_HOST", "127.0.0.1")
SCAN_INTERVAL    = 15.0
CDP_RECV_TIMEOUT = 6
CDP_RECV_RETRIES = 10

# Intern: Welche Pages haben den Observer bereits injiziert (page_id → url)
_observer_injected: WeakKeyDictionary[Any, str] = WeakKeyDictionary()


# ══════════════════════════════════════════════════════════════════
#  PHASE 1: OBSERVER-INJEKTION  (einmalig pro Page/URL)
# ══════════════════════════════════════════════════════════════════

JS_INJECT_OBSERVERS = r"""
(function() {
if (window.__csi) return "already_injected";

window.__csi = {
    geetest: { solved: false, ts: 0, challenge: "", validate: "", seccode: "" },
};

function checkGeeTestTriad() {
    const c = document.querySelector('input[name="geetest_challenge"]');
    const v = document.querySelector('input[name="geetest_validate"]');
    const s = document.querySelector('input[name="geetest_seccode"]');
    if (c && v && s && c.value && v.value && s.value) {
        window.__csi.geetest = {
            solved: true, ts: Date.now(),
            challenge: c.value, validate: v.value, seccode: s.value,
        };
    } else if (window.__csi.geetest.solved && !(c || v || s)) {
        window.__csi.geetest = { solved: false, ts: 0, challenge: "", validate: "", seccode: "" };
    }
}

const obs = new MutationObserver(() => {
    checkGeeTestTriad();
});
obs.observe(document.documentElement, {
    childList: true, subtree: true,
});

checkGeeTestTriad();

return "injected";
})();
"""


# ══════════════════════════════════════════════════════════════════
#  PHASE 2: DEEP SCAN  (läuft jedes Scan-Intervall)
# ══════════════════════════════════════════════════════════════════

JS_DEEP_SCAN = r"""
(function() {
"use strict";

// ════════════════════════════════════════
//  UTILITY
// ════════════════════════════════════════

function isVisible(el) {
    if (!el) return false;
    try {
        const s = window.getComputedStyle(el);
        if (s.display === "none" || s.visibility === "hidden") return false;
        if (parseFloat(s.opacity) < 0.05) return false;
        if (el.getAttribute("aria-hidden") === "true") return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    } catch(e) { return true; }
}

function deepQuery(root, sel) {
    try { const el = root.querySelector(sel); if (el) return el; } catch(e) {}
    try {
        for (const el of root.querySelectorAll("*")) {
            if (el.shadowRoot) { const f = deepQuery(el.shadowRoot, sel); if (f) return f; }
        }
    } catch(e) {}
    return null;
}

function deepQueryVisible(root, sel) {
    try { for (const el of root.querySelectorAll(sel)) { if (isVisible(el)) return el; } } catch(e) {}
    try {
        for (const el of root.querySelectorAll("*")) {
            if (el.shadowRoot) { const f = deepQueryVisible(el.shadowRoot, sel); if (f) return f; }
        }
    } catch(e) {}
    return null;
}

function collectDocs(win, depth) {
    if (depth > 1) return [];
    let docs = [];
    try { if (win.document) docs.push(win.document); } catch(e) {}
    try {
        for (let i = 0; i < win.frames.length; i++) {
            try { docs = docs.concat(collectDocs(win.frames[i], depth + 1)); } catch(e) {}
        }
    } catch(e) {}
    return docs;
}

// ── Einmalige Datensammlung ──────────────────────────────────────

let allDocs = [];
let iframeSrcs = [];
let scriptSrcs = [];
let currentPath = "";
let currentHostname = "";

function updateGlobals() {
    allDocs = collectDocs(window, 0);
    iframeSrcs = [];
    scriptSrcs = [];
    for (const doc of allDocs) {
        try {
            for (const fr of doc.querySelectorAll("iframe[src]")) {
                const s = (fr.src || "").toLowerCase();
                if (s) iframeSrcs.push(s);
            }
            for (const s of doc.querySelectorAll("script[src]")) {
                const src = (s.src || "").toLowerCase();
                if (src) scriptSrcs.push(src);
            }
        } catch(e) {}
    }
    currentPath = window.location.pathname.toLowerCase();
    currentHostname = window.location.hostname.toLowerCase();
}

const iframeHas     = p  => iframeSrcs.some(s => s.includes(p));
const scriptHas     = p  => scriptSrcs.some(s => s.includes(p));
// Regex-Prüfung auf Iframe-URLs verhindert Substring-Fehlmatches
const iframeMatchRe = re => iframeSrcs.some(s => re.test(s));

// Observer-State (von Phase 1 injiziert)
const csi = window.__csi || {
    geetest: { solved: false },
};


// ════════════════════════════════════════
//  BASE PROVIDER
// ════════════════════════════════════════

class CaptchaProvider {
    constructor(name) { this.name = name; }
    detect() { return null; }
    result(extra) {
        return { name: this.name, solved: false, description: "", ...extra };
    }
}


// ════════════════════════════════════════
//  PROVIDER: Microsoft HIP / Press & Hold
//  Priorität 1 – muss VOR FunCaptcha laufen
// ════════════════════════════════════════

class MicrosoftHIPProvider extends CaptchaProvider {
    constructor() { super("Microsoft HIP"); }

    detect() {
        const hasMsIframe = ["challenges.microsoft.com","client.hip.live.com","account.microsoft.com/captcha"]
            .some(d => iframeHas(d));

        let hasMsDOM = false;
        for (const doc of allDocs) {
            for (const sel of ["#hipTemplateDiv","#captchaContainer",".atsl-captcha",
                               "div[data-ms-captcha]","#HipImgContainer","#hipImageInput"]) {
                if (deepQuery(doc, sel)) { hasMsDOM = true; break; }
            }
            if (hasMsDOM) break;
        }

        const msPhrases = ["halten sie die schaltfläche","press and hold",
                           "zugängliche herausforderung","hipaction","hip.live.com","hiptemplatediv"];
        const docText = document.body ? document.body.textContent.toLowerCase() : "";
        const hasMsText = msPhrases.some(p => docText.includes(p));

        const onMsDomain = /\.(microsoft|live|microsoftonline|xbox)\.com$/.test(currentHostname);
        const hasMsCaptchaText = ["lassen sie uns beweisen, dass sie menschlich sind",
                                  "prove you're not a robot","menschlich sind"]
            .some(t => docText.includes(t));

        if (!hasMsIframe && !hasMsDOM && !hasMsText && !(onMsDomain && hasMsCaptchaText)) return null;

        let subtype = "HIP";
        if (docText.includes("halten sie die schaltfläche") || docText.includes("press and hold")) {
            subtype = "Press & Hold";
        } else if (hasMsDOM && deepQuery(allDocs[0], "#HipImgContainer")) {
            subtype = "Bild-CAPTCHA";
        }

        return this.result({
            name: `Microsoft HIP – ${subtype}`,
            description: `Microsoft HIP CAPTCHA – Variante: ${subtype}.\n  ➜ Schaltfläche halten bis 100%.`,
        });
    }
}


// ════════════════════════════════════════
//  PROVIDER: reCAPTCHA v3 / Invisible
// ════════════════════════════════════════

class ReCaptchaV3Provider extends CaptchaProvider {
    constructor() { super("reCAPTCHA v3 / Invisible"); }

    detect() {
        let hasGrecaptchaHTML = false;
        try { hasGrecaptchaHTML = document.documentElement.outerHTML.includes("grecaptcha.execute("); } catch(e) {}
        const hasV3Script =
            scriptHas("recaptcha/api.js?render=") ||
            scriptHas("recaptcha.net/recaptcha/api.js?render=") ||
            hasGrecaptchaHTML;

        let hasBadge = false;
        for (const doc of allDocs) {
            if (deepQueryVisible(doc, ".grecaptcha-badge")) { hasBadge = true; break; }
        }
        if (!hasV3Script && !hasBadge) return null;

        // v3 zurückziehen wenn v2-Anchor sichtbar
        for (const doc of allDocs) {
            if (deepQuery(doc, "#recaptcha-anchor, .rc-anchor-container")) return null;
        }
        return this.result({
            description: "reCAPTCHA v3 / Invisible – Score-basiert, läuft im Hintergrund.",
        });
    }
}


// ════════════════════════════════════════
//  PROVIDER: reCAPTCHA v2
//  Dreistufige Erkennungs-Hierarchie:
//    [A] Iframe von google.com/recaptcha/(api2|enterprise)/ oder recaptcha.net
//        → Regex-Prüfung verhindert Substring-Matches auf falschen Domains
//    [B] #recaptcha-anchor / .rc-anchor-container im DOM
//        → erscheinen nur in echten reCAPTCHA-Frames
//    [C] .g-recaptcha[data-sitekey] mit Google-Sitekey-Präfix "6L" + Script
//        → hCaptcha nutzt manchmal g-recaptcha-Klasse → expliziter Ausschluss
//  Negativfilter: Fake-CAPTCHA-Sites (Win+R Malware)
// ════════════════════════════════════════

class ReCaptchaV2Provider extends CaptchaProvider {
    constructor() { super("reCAPTCHA v2"); }

    detect() {
        const FAKE = ["win + r","windows + r","ctrl + v","press windows",
                      "paste into run","paste in run","type into run","open run dialog","winkey + r"];
        const docText = document.body ? document.body.textContent.toLowerCase() : "";
        if (FAKE.some(s => docText.includes(s))) return null;

        // [A] Authentischer Google-Iframe (exakter Pfad-Regex)
        const IFRAME_RE = /\/(google\.com|recaptcha\.net)\/recaptcha\/(api2|enterprise)\//;
        const hasAuthoritativeIframe = iframeMatchRe(IFRAME_RE);

        // [B] Anchor-Element (entsteht nur in echten reCAPTCHA-Frames)
        let hasAnchor = false;
        for (const doc of allDocs) {
            if (deepQuery(doc, "#recaptcha-anchor") || deepQuery(doc, ".rc-anchor-container")) {
                hasAnchor = true; break;
            }
        }

        // [C] Div mit verifiziertem Sitekey + reCAPTCHA-Script
        let divWithValidSitekey = false;
        const hCaptchaPresent = iframeHas("hcaptcha.com") || scriptHas("hcaptcha.com");
        if (!hCaptchaPresent) {
            const hasRcScript = scriptHas("google.com/recaptcha/api.js") ||
                                scriptHas("recaptcha.net/recaptcha/api.js");
            if (hasRcScript) {
                for (const doc of allDocs) {
                    const divs = doc.querySelectorAll
                        ? doc.querySelectorAll(".g-recaptcha[data-sitekey]") : [];
                    for (const div of divs) {
                        if ((div.getAttribute("data-sitekey") || "").startsWith("6L") && isVisible(div)) {
                            divWithValidSitekey = true; break;
                        }
                    }
                    if (divWithValidSitekey) break;
                }
            }
        }

        if (!hasAuthoritativeIframe && !hasAnchor && !divWithValidSitekey) return null;

        let solved = false;
        for (const doc of allDocs) {
            const anchor = deepQuery(doc, "#recaptcha-anchor");
            if (anchor?.getAttribute("aria-checked") === "true") { solved = true; break; }
            const resp = deepQuery(doc, "textarea[name='g-recaptcha-response']");
            if (resp?.value?.length > 20) { solved = true; break; }
        }

        return this.result({
            solved,
            description: "reCAPTCHA v2 – Checkbox oder Bild-Rätsel (Google).\n  ➜ Checkbox klicken; bei Rätsel alle passenden Felder auswählen.",
        });
    }
}


// ════════════════════════════════════════
//  PROVIDER: hCaptcha
// ════════════════════════════════════════

class HCaptchaProvider extends CaptchaProvider {
    constructor() { super("hCaptcha"); }

    detect() {
        const hasHCIframe = iframeHas("hcaptcha.com");
        const hasHCScript = scriptHas("hcaptcha.com/1/api.js") || scriptHas("js.hcaptcha.com");
        let hasHCDiv = false;
        for (const doc of allDocs) {
            if (deepQuery(doc, ".h-captcha[data-sitekey]") ||
                deepQuery(doc, "div[data-hcaptcha-widget-id]")) { hasHCDiv = true; break; }
        }
        if (!hasHCIframe && !hasHCScript && !hasHCDiv) return null;

        let solved = false;
        for (const doc of allDocs) {
            const r = deepQuery(doc, "textarea[name='h-captcha-response']");
            if (r?.value?.length > 20) { solved = true; break; }
        }
        return this.result({
            solved,
            description: "hCaptcha – Checkbox oder Bild-Rätsel.\n  ➜ Checkbox klicken → Bild-Rätsel lösen.",
        });
    }
}


// ════════════════════════════════════════
//  PROVIDER: Cloudflare Turnstile
// ════════════════════════════════════════

class TurnstileProvider extends CaptchaProvider {
    constructor() { super("Cloudflare Turnstile"); }

    detect() {
        const hasTsIframe = iframeHas("challenges.cloudflare.com/turnstile");
        const hasTsScript = scriptHas("challenges.cloudflare.com/turnstile");
        let hasTsDiv = false;
        for (const doc of allDocs) {
            if (deepQuery(doc, ".cf-turnstile, div[data-cf-turnstile]")) { hasTsDiv = true; break; }
        }
        if (!hasTsIframe && !hasTsScript && !hasTsDiv) return null;

        let solved = false;
        for (const doc of allDocs) {
            const r = deepQuery(doc, "input[name='cf-turnstile-response']");
            if (r?.value?.length > 20) { solved = true; break; }
        }
        return this.result({
            solved,
            description: "Cloudflare Turnstile – Verhaltensbasiertes Widget.",
        });
    }
}


// ════════════════════════════════════════
//  PROVIDER: Cloudflare Challenge (klassisch)
// ════════════════════════════════════════

class CfChallengeProvider extends CaptchaProvider {
    constructor() { super("Cloudflare Challenge"); }

    detect() {
        let found = false;
        for (const doc of allDocs) {
            if (deepQuery(doc, "div#challenge-form, form[action*='__cf_chl'], #challenge-running, #challenge-spinner")) {
                found = true; break;
            }
        }
        if (!found) return null;
        return this.result({
            description: "Cloudflare Security Challenge.\n  ➜ Kurz warten – löst sich meist von selbst.",
        });
    }
}


// ArkoseProvider (FunCaptcha) entfernt – nicht mehr unterstützt.


// ════════════════════════════════════════════════════════════════════
//  TIKTOK CAPTCHA – Drei vollständig getrennte Provider
//
//  Mechanismus-Überblick:
//
//  ┌─────────────────────────────────────────────────────────────┐
//  │ Typ           │ Struktur                  │ Interaktion     │
//  ├─────────────────────────────────────────────────────────────┤
//  │ Rotate        │ Zwei konzentrische Bilder │ Slider → Drehen │
//  │               │ (inner + outer, kreisförmig)│               │
//  ├─────────────────────────────────────────────────────────────┤
//  │ Puzzle Slide  │ Puzzleteil + Hintergrund  │ Slider → Ziehen │
//  │               │ mit Lücke (rechteckig)    │                 │
//  ├─────────────────────────────────────────────────────────────┤
//  │ 3D Objects    │ Ein Bild mit 3D-Objekten  │ Direkte Klicks  │
//  │               │ (Buchstaben/Zahlen/Formen)│ + Submit-Button │
//  └─────────────────────────────────────────────────────────────┘
//
//  Wichtig: Alle drei Typen teilen #captcha-verify-image.
//  Differenzierung ausschließlich über Slider-Präsenz, Bild-Struktur
//  und Submit-Button. Rotate wird zuerst geprüft (eindeutigste Signale),
//  dann Slide (Slider + Puzzleteil), zuletzt 3D Objects (Submit, kein Slider).
//
//  Jeder Provider wird einzeln im Orchestrator registriert.
//  Falls mehrere matchen (sollte nicht vorkommen), gilt die Priorität
//  der Provider-Liste: Rotate > Slide > 3D Objects.
// ════════════════════════════════════════════════════════════════════


// ────────────────────────────────────────
//  Hilfsfunktion: TikTok-Container vorhanden?
//  Wird von allen drei TikTok-Providern aufgerufen, um
//  Mehrfach-Traversal zu vermeiden.
// ────────────────────────────────────────
function _tikTokContainerPresent() {
    // Iframe-Prüfung (ByteDance CDN / TikTok eigene Domains)
    if (iframeHas("tiktok.com/captcha") ||
        iframeHas("verification.bytedance.com") ||
        iframeHas("verify.bytedance.com") ||
        iframeHas("verify.tiktok.com")) return true;

    // DOM-Container: V1 (.captcha-disable-scroll) oder V2 (.captcha-verify-container)
    const containerSelectors = [
        ".captcha-disable-scroll",
        ".captcha-verify-container",
        ".captcha_verify_container",
        "div[id*='tiktok-captcha']",
        "div[class*='secsdk_captcha']",
        "div[class*='TUXCaptcha']",
    ];
    for (const doc of allDocs) {
        for (const sel of containerSelectors) {
            if (deepQueryVisible(doc, sel)) return true;
        }
    }
    return false;
}


// ════════════════════════════════════════
//  PROVIDER: TikTok Rotate CAPTCHA
//
//  Mechanismus:
//    Zwei konzentrische, kreisförmige Bilder werden überlagert.
//    Das innere Bild ist rotiert (falsch ausgerichtet), der Nutzer
//    muss über einen Slider das innere Bild in die korrekte Position
//    zurückdrehen. Das äußere Bild dient als statische Referenz.
//
//  Eindeutige DOM-Merkmale:
//    – ZWEI Bilder (inner + outer) im selben Container
//    – Slider-Element zum Drehen (kein Puzzleteil-Bild!)
//    – V1: data-testid="whirl-inner-img" / "whirl-outer-img"
//    – V2: img.cap-absolute (inneres Bild überlagert erstes Bild)
//
//  Abgrenzung zu Puzzle Slide:
//    Rotate hat zwei überlagerte Bild-Elemente im kreisförmigen
//    Container, kein img.captcha_verify_img_slide (Puzzleteil).
//
//  Selektoren:
//    V1 inneres Bild : [data-testid=whirl-inner-img]
//    V1 äußeres Bild : [data-testid=whirl-outer-img]
//    V1 Slider       : .secsdk-captcha-drag-icon
//    V2 inneres Bild : .captcha-verify-container > div > div > div > img.cap-absolute
//    V2 äußeres Bild : .captcha-verify-container > div > div > div > img:first-child
//    V2 Slider       : .captcha-verify-container div[draggable=true]
//    Klassen-Fallback: div[class*='captcha_verify_rotate'], .secsdk_captcha_rotate
// ════════════════════════════════════════

class TikTokRotateProvider extends CaptchaProvider {
    constructor() { super("TikTok CAPTCHA (Rotate)"); }

    detect() {
        if (!_tikTokContainerPresent()) return null;

        for (const doc of allDocs) {
            // ── V1: data-testid-Selektoren (Legacy-API) ──────────────
            const innerV1 = deepQueryVisible(doc, "[data-testid='whirl-inner-img']");
            const outerV1 = deepQueryVisible(doc, "[data-testid='whirl-outer-img']");
            const sliderV1 = deepQueryVisible(doc, ".secsdk-captcha-drag-icon") ||
                             deepQueryVisible(doc, ".secsdk_captcha_rotate");
            const classRotate = deepQueryVisible(doc, "div[class*='captcha_verify_rotate']");

            // V1: beide Bild-Testids → eindeutig Rotate
            if (innerV1 && outerV1) {
                return this.result({
                    description: [
                        "Rotate CAPTCHA (V1) – Inneres Bild ist rotiert.",
                        "  ➜ Slider (.secsdk-captcha-drag-icon) horizontal ziehen bis",
                        "    das innere Bild [data-testid=whirl-inner-img] korrekt ausgerichtet ist.",
                        "  ➜ Lösungsweg: Screenshot inneres + äußeres Bild → SadCaptcha-API",
                        "    liefert Winkel → Slider um entsprechenden Pixel-Offset verschieben.",
                    ].join("\n"),
                });
            }

            // V1: Klassen-Fallback (ältere Implementierung ohne testids)
            if (classRotate && sliderV1) {
                return this.result({
                    description: [
                        "Rotate CAPTCHA (V1 Klassen-Fallback) – Rotiertes Kreisbild.",
                        "  ➜ Slider (.secsdk-captcha-drag-icon) ziehen.",
                        "  ➜ Container: div[class*='captcha_verify_rotate']",
                    ].join("\n"),
                });
            }

            // ── V2: cap-absolute-Selektor (aktuelle API) ─────────────
            // Das innere (rotierende) Bild liegt als img.cap-absolute
            // über dem ersten img-Element (äußeres Bild) im Container.
            const innerV2 = deepQueryVisible(doc,
                ".captcha-verify-container > div > div > div > img.cap-absolute");
            const outerV2 = deepQueryVisible(doc,
                ".captcha-verify-container > div > div > div > img:first-child");
            const sliderV2 = deepQueryVisible(doc,
                ".captcha-verify-container div[draggable='true']");

            if (innerV2 && outerV2 && sliderV2) {
                return this.result({
                    description: [
                        "Rotate CAPTCHA (V2) – Zwei überlagerte Kreisbilder.",
                        "  ➜ Slider (.captcha-verify-container div[draggable=true]) ziehen.",
                        "  ➜ Inneres Bild: img.cap-absolute  |  Äußeres: img:first-child",
                        "  ➜ Lösungsweg: Screenshot beider Bilder → Winkel-API →",
                        "    mouse.down() → mouse.move(slider_x + offset) → mouse.up()",
                    ].join("\n"),
                });
            }

            // V2 Fallback: inneres Bild vorhanden + Slider (äußeres nicht sicher erkannt)
            if (innerV2 && sliderV2) {
                return this.result({
                    description: [
                        "Rotate CAPTCHA (V2 Partial) – Inneres Bild + Slider erkannt.",
                        "  ➜ Slider (.captcha-verify-container div[draggable=true]) ziehen.",
                    ].join("\n"),
                });
            }
        }
        return null;
    }
}


// ════════════════════════════════════════
//  PROVIDER: TikTok Puzzle Slide CAPTCHA
//
//  Mechanismus:
//    Ein rechteckiges Hintergrundbild hat eine Lücke (Puzzleform).
//    Ein separates Puzzleteil-Bild muss per Slider horizontal
//    über die Lücke geschoben werden. Slider + Puzzleteil sind
//    das eindeutige Merkmal – KEIN Submit-Button.
//
//  Eindeutige DOM-Merkmale:
//    – Puzzleteil als separates img-Element (captcha_verify_img_slide)
//    – Hintergrundbild mit Lücke (#captcha-verify-image)
//    – Slider zum horizontalen Verschieben (.secsdk-captcha-drag-icon)
//    – V2: draggable-div als Puzzleteil-Container
//    – KEIN .verify-captcha-submit-button
//    – KEIN whirl-inner/outer-img (kein konzentrisches Bild)
//
//  Abgrenzung zu Rotate:
//    Kein konzentrisches Doppelbild; Puzzleteil ist ein eigenes
//    img-Element neben dem Hintergrund (nicht überlagert).
//
//  Selektoren:
//    V1 Puzzleteil   : img.captcha_verify_img_slide
//    V1 Hintergrund  : #captcha-verify-image
//    V1 Slider       : .secsdk-captcha-drag-icon
//    V2 Puzzleteil   : .captcha-verify-container .cap-absolute img
//    V2 Hintergrund  : #captcha-verify-image
//    V2 Slider       : .secsdk-captcha-drag-icon (gleiches Element)
//    Klassen-Fallback: div[class*='captcha_verify_slide'], .secsdk_captcha_slide
// ════════════════════════════════════════

class TikTokSlideProvider extends CaptchaProvider {
    constructor() { super("TikTok CAPTCHA (Puzzle Slide)"); }

    detect() {
        if (!_tikTokContainerPresent()) return null;

        for (const doc of allDocs) {
            // Slider-Präsenz ist Grundbedingung (trennt von 3D Objects)
            const hasSlider =
                deepQueryVisible(doc, ".secsdk-captcha-drag-icon") ||
                deepQueryVisible(doc, ".captcha-verify-container div[draggable='true']");
            if (!hasSlider) continue;

            // ── V1: Klassisches Puzzleteil-img ───────────────────────
            // img.captcha_verify_img_slide ist exklusiv für Puzzle Slide –
            // Rotate nutzt whirl-inner/outer-img stattdessen.
            const puzzlePieceV1 = deepQueryVisible(doc, "img.captcha_verify_img_slide");
            const bgImageV1     = deepQueryVisible(doc, "#captcha-verify-image");
            const classSlide    = deepQueryVisible(doc, "div[class*='captcha_verify_slide']") ||
                                  deepQueryVisible(doc, ".secsdk_captcha_slide");

            if (puzzlePieceV1 && bgImageV1) {
                return this.result({
                    description: [
                        "Puzzle Slide CAPTCHA (V1) – Puzzleteil in Lücke schieben.",
                        "  ➜ Puzzleteil: img.captcha_verify_img_slide",
                        "  ➜ Hintergrund mit Lücke: #captcha-verify-image",
                        "  ➜ Slider: .secsdk-captcha-drag-icon  (horizontal ziehen)",
                        "  ➜ Lösungsweg: Template-Matching (Puzzleteil vs. Hintergrund)",
                        "    → Pixel-Offset berechnen → Slider entsprechend verschieben.",
                    ].join("\n"),
                });
            }

            if (classSlide && hasSlider) {
                return this.result({
                    description: [
                        "Puzzle Slide CAPTCHA (V1 Klassen-Fallback).",
                        "  ➜ Container: div[class*='captcha_verify_slide']",
                        "  ➜ Slider: .secsdk-captcha-drag-icon  (horizontal ziehen)",
                    ].join("\n"),
                });
            }

            // ── V2: cap-absolute-Puzzleteil ──────────────────────────
            // In V2 liegt das Puzzleteil als img innerhalb eines
            // .cap-absolute-Containers über dem Hintergrundbild.
            // Abgrenzung zu Rotate-V2: img.cap-absolute direkt im
            // Tier-4-Container ist Rotate; hier ist es ein img INNERHALB
            // von .cap-absolute (ein Level tiefer).
            const puzzlePieceV2 = deepQueryVisible(doc,
                ".captcha-verify-container .cap-absolute img");
            const bgImageV2 = deepQueryVisible(doc, "#captcha-verify-image");

            // Sicherheitscheck: Rotate-V1/V2-Signale ausschließen
            const hasRotateSignal =
                deepQueryVisible(doc, "[data-testid='whirl-inner-img']") ||
                deepQueryVisible(doc, ".captcha-verify-container > div > div > div > img.cap-absolute");

            if (puzzlePieceV2 && bgImageV2 && !hasRotateSignal) {
                return this.result({
                    description: [
                        "Puzzle Slide CAPTCHA (V2) – Puzzleteil (.cap-absolute img) schieben.",
                        "  ➜ Hintergrund mit Lücke: #captcha-verify-image",
                        "  ➜ Slider: .captcha-verify-container div[draggable=true]",
                        "  ➜ Lösungsweg: Screenshot Puzzleteil + Hintergrund →",
                        "    Template-Matching → Offset → mouse.move() mit steps.",
                    ].join("\n"),
                });
            }
        }
        return null;
    }
}


// ════════════════════════════════════════
//  PROVIDER: TikTok 3D Objects / Shapes CAPTCHA
//
//  Mechanismus:
//    Ein einzelnes Bild zeigt mehrere 3D-gerenderte Objekte
//    (Buchstaben, Zahlen, geometrische Formen) in einem Raum.
//    Eine Textanweisung gibt vor, welche Objekte in welcher
//    Reihenfolge angeklickt werden müssen. Nach allen Klicks
//    wird über einen Submit-Button abgesendet.
//    KEIN Slider, KEIN zweites Bild-Element.
//
//  Eindeutige DOM-Merkmale:
//    – Ein Hauptbild mit allen 3D-Objekten (#captcha-verify-image)
//    – Submit-Button (.verify-captcha-submit-button)
//    – Textanweisung (.captcha_verify_bar)
//    – V2: button.cap-w-full als Submit, span als Anweisung
//    – KEIN .secsdk-captcha-drag-icon (kein Slider!)
//    – KEIN img.captcha_verify_img_slide (kein Puzzleteil!)
//    – KEIN whirl-inner/outer-img (kein konzentrisches Bild!)
//
//  Abgrenzung:
//    Das Fehlen des Sliders ist das stärkste Abgrenzungsmerkmal
//    zu Rotate und Puzzle Slide. Zusätzlich ist der Submit-Button
//    exklusiv für diesen Typ.
//
//  Selektoren:
//    V1 Bild         : #captcha-verify-image
//    V1 Submit       : .verify-captcha-submit-button
//    V1 Anweisung    : .captcha_verify_bar
//    V2 Bild         : .captcha-verify-container div.cap-relative img
//    V2 Submit       : .captcha-verify-container .cap-relative button.cap-w-full
//    V2 Anweisung    : .captcha-verify-container > div > div > span
//    Negativfilter   : .secsdk-captcha-drag-icon (→ kein 3D Objects!)
// ════════════════════════════════════════

class TikTok3DObjectsProvider extends CaptchaProvider {
    constructor() { super("TikTok CAPTCHA (3D Objects)"); }

    detect() {
        if (!_tikTokContainerPresent()) return null;

        for (const doc of allDocs) {
            // ── Negativfilter: Slider vorhanden → nicht 3D Objects ───
            // Dieser Check muss als erstes laufen, da #captcha-verify-image
            // bei allen drei TikTok-Typen erscheint.
            const hasSlider =
                deepQueryVisible(doc, ".secsdk-captcha-drag-icon") ||
                deepQueryVisible(doc, ".captcha-verify-container div[draggable='true']");
            if (hasSlider) continue;  // Rotate oder Slide → überspringen

            // ── Negativfilter: Rotate-Bilder vorhanden → nicht 3D ────
            const hasRotateParts =
                deepQueryVisible(doc, "[data-testid='whirl-inner-img']") ||
                deepQueryVisible(doc, "[data-testid='whirl-outer-img']") ||
                deepQueryVisible(doc, ".captcha-verify-container > div > div > div > img.cap-absolute");
            if (hasRotateParts) continue;

            // ── V1: klassischer Submit-Button + Hauptbild ─────────────
            const submitV1 = deepQueryVisible(doc, ".verify-captcha-submit-button");
            const imageV1  = deepQueryVisible(doc, "#captcha-verify-image");
            const instrV1  = deepQueryVisible(doc, ".captcha_verify_bar");

            if (submitV1 && imageV1) {
                const hasInstruction = instrV1 ? "  ➜ Anweisung in: .captcha_verify_bar" : "";
                return this.result({
                    description: [
                        "3D Objects CAPTCHA (V1) – 3D-Objekte in Reihenfolge anklicken.",
                        "  ➜ Bild: #captcha-verify-image  (enthält alle Objekte)",
                        "  ➜ Submit: .verify-captcha-submit-button  (nach allen Klicks)",
                        hasInstruction,
                        "  ➜ Lösungsweg: Screenshot → SadCaptcha-API liefert",
                        "    relative (x,y)-Koordinaten pro Objekt → page.mouse.click()",
                        "    mit Zufallsoffset (±3–5 px) + Pause 500–1500 ms zwischen Klicks.",
                    ].filter(Boolean).join("\n"),
                });
            }

            // ── V2: button.cap-w-full + div.cap-relative img ──────────
            const submitV2 = deepQueryVisible(doc,
                ".captcha-verify-container .cap-relative button.cap-w-full");
            const imageV2  = deepQueryVisible(doc,
                ".captcha-verify-container div.cap-relative img");
            const instrV2  = deepQueryVisible(doc,
                ".captcha-verify-container > div > div > span");

            if (submitV2 && imageV2) {
                return this.result({
                    description: [
                        "3D Objects CAPTCHA (V2) – 3D-Objekte in Reihenfolge anklicken.",
                        "  ➜ Bild: .captcha-verify-container div.cap-relative img",
                        "  ➜ Submit: button.cap-w-full  (nach allen Klicks absenden)",
                        instrV2 ? "  ➜ Anweisung: .captcha-verify-container > div > div > span" : "",
                        "  ➜ Lösungsweg: Screenshot → SadCaptcha-API liefert",
                        "    relative (x,y)-Koordinaten → page.mouse.click() mit",
                        "    Zufallsoffset + menschlicher Pause zwischen Klicks.",
                    ].filter(Boolean).join("\n"),
                });
            }

            // ── Fallback: TUXCaptcha / Shapes-Klassenname ────────────
            const hasTUX =
                deepQueryVisible(doc, "div[class*='TUXCaptcha']") ||
                deepQueryVisible(doc, "div[class*='captcha_shapes']") ||
                deepQueryVisible(doc, "div[class*='captcha_verify_img_3d']") ||
                deepQueryVisible(doc, ".captcha_verify_img--wrapper");

            if (hasTUX) {
                return this.result({
                    description: [
                        "3D Objects CAPTCHA (TUX/Shapes-Fallback).",
                        "  ➜ Container: div[class*='TUXCaptcha'] oder captcha_shapes",
                        "  ➜ Kein Slider erkannt → Submit-Button-basierte Interaktion.",
                    ].join("\n"),
                });
            }
        }
        return null;
    }
}


// ════════════════════════════════════════
//  PROVIDER: GeeTest v3 / v4
// ════════════════════════════════════════

class GeeTestProvider extends CaptchaProvider {
    constructor() { super("GeeTest"); }

    detect() {
        let found = false;
        for (const doc of allDocs) {
            for (const sel of ["div.geetest_holder","div.geetest_wrap",".geetest_btn",
                               "div.geetest_box","div.geetest_oneclick_wrap"]) {
                if (deepQueryVisible(doc, sel)) { found = true; break; }
            }
            if (found) break;
        }
        const hasGTScript = scriptHas("static.geetest.com") || scriptHas("gcaptcha.geetest.com") ||
                            scriptHas("gcaptcha4.geetest.com");
        if (!found && !hasGTScript) return null;

        const isV4 = scriptHas("gcaptcha4.geetest.com") || scriptHas("static.geetest.com/v4");

        let solved = csi.geetest?.solved || false;
        if (!solved) {
            for (const doc of allDocs) {
                const c = deepQuery(doc, 'input[name="geetest_challenge"]');
                const v = deepQuery(doc, 'input[name="geetest_validate"]');
                const s = deepQuery(doc, 'input[name="geetest_seccode"]');
                if (c?.value && v?.value && s?.value) { solved = true; break; }
                if (deepQuery(doc, ".geetest_panel_success, .geetest_success_radar_tip")) { solved = true; break; }
            }
        }
        return this.result({
            name: isV4 ? "GeeTest v4" : "GeeTest v3",
            solved,
            description: isV4
                ? "GeeTest v4 – One-Click oder AI-Verification."
                : "GeeTest v3 – Slider-Puzzle.\n  ➜ Slider ziehen bis Puzzleteil in Lücke passt.",
        });
    }
}


// MTCaptchaProvider entfernt – nicht mehr unterstützt.


// ClickCaptchaProvider entfernt – nicht mehr unterstützt.


// ════════════════════════════════════════
//  PROVIDER: Meta Text-CAPTCHA
//  Instagram Checkpoint + Facebook Security Check
//
//  FALSE-POSITIVE-FIX (v6):
//  Ursache: Facebook-Registrierungsseite (facebook.com/r.php) wurde
//  erkannt weil onFbDomain allein als ausreichendes Signal galt.
//
//  Neue Logik – zwei Schutzebenen:
//
//  Ebene 1 – URL-Negativfilter:
//    Bekannte Non-CAPTCHA-Pfade (Registrierung, Login, Passwort-Reset)
//    werden ohne starkes Primärsignal grundsätzlich ausgeschlossen.
//    Starkes Primärsignal = captcha_response-Input im DOM.
//
//  Ebene 2 – Pflicht-Explizit-Signal:
//    Auf Meta-Domains (FB/IG) muss mindestens EINES der folgenden
//    expliziten CAPTCHA-Elemente vorhanden sein:
//      – hasCaptchaResponse: input[name=captcha_response/token/sid]
//      – hasFbSecurity: Security-Check-Container ODER CAPTCHA-Formular
//      – hasIgCheckpoint: iframe von instagram.com/checkpoint/
//      – hasIgSuspended: /accounts/suspended/ mit CAPTCHA-Bild
//      – hasMetaCaptchaImg: Bild mit CAPTCHA-typischer URL/Alt + CAPTCHA-Input
// ════════════════════════════════════════

class MetaTextCaptchaProvider extends CaptchaProvider {
    constructor() { super("Text-CAPTCHA (Meta)"); }

    detect() {
        const onFbDomain = /\.(facebook|fb)\.com$/.test(currentHostname);
        const onIgDomain = currentHostname.includes("instagram.com");

        if (!onFbDomain && !onIgDomain) return null;

        // ── Ebene 1: URL-Negativfilter ───────────────────────────────
        // Diese Pfade sind normale Account-Seiten ohne CAPTCHA-Formular.
        const NON_CAPTCHA_PATHS = [
            "/r.php", "/reg", "/signup", "/create",     // Registrierung
            "/login", "/accounts/login",                 // Login
            "/accounts/emailsignup",                     // IG Registrierung
            "/recover", "/forgot", "/password",          // Passwort-Reset
        ];
        const isNonCaptchaPage = NON_CAPTCHA_PATHS.some(p => currentPath.startsWith(p));

        // ── Explizite CAPTCHA-Signale ────────────────────────────────

        // Stärkstes Signal: Meta-typische Input-Namen im DOM
        let hasCaptchaResponse = false;
        for (const doc of allDocs) {
            if (deepQuery(doc, "input[name='captcha_response'], input[name='captcha_token'], input[name='captcha_sid']")) {
                hasCaptchaResponse = true; break;
            }
        }

        // Instagram Checkpoint-Iframe
        const hasIgCheckpoint = iframeHas("instagram.com/checkpoint/");
        let hasIgFrame = false;
        for (const doc of allDocs) {
            if (deepQuery(doc, "iframe[src*='instagram.com/checkpoint']")) { hasIgFrame = true; break; }
        }

        // Facebook Security Check – nur in dedizierten CAPTCHA-Containern suchen,
        // NICHT im gesamten Body-Text (würde bei /r.php fälschlich matchen)
        let hasFbSecurity = false;
        for (const doc of allDocs) {
            for (const sel of [".hidden_elem", "#captcha", "#captcha_response_wrapper"]) {
                const el = deepQuery(doc, sel);
                if (el?.textContent?.toLowerCase().includes("security check")) {
                    hasFbSecurity = true; break;
                }
            }
            if (hasFbSecurity) break;
            // Explizites CAPTCHA-Formular (eindeutigstes FB-Signal)
            if (deepQuery(doc, "form[action*='/captcha'], #captcha_response_wrapper")) {
                hasFbSecurity = true; break;
            }
        }

        // Instagram gesperrter Account mit Text-CAPTCHA
        // /accounts/suspended/ zeigt ein CAPTCHA-Bild + Eingabefeld
        const hasIgSuspended = onIgDomain && (
            currentPath.includes("/accounts/suspended") ||
            currentPath.includes("/challenge/")
        );

        // Allgemeines Meta-CAPTCHA-Bild + passender Input
        // (fängt neue Checkpoint-Varianten ab die keinen expliziten Namen haben)
        let hasMetaCaptchaImg = false;
        let hasMetaCaptchaInput = false;
        for (const doc of allDocs) {
            for (const sel of [
                "img[src*='captcha' i]","img[src*='challenge' i]",
                "img[alt*='captcha' i]","img[alt*='verification' i]",
                "img[class*='captcha' i]",
            ]) {
                if (deepQueryVisible(doc, sel)) { hasMetaCaptchaImg = true; break; }
            }
            for (const sel of [
                "input[placeholder*='abbildung' i]",
                "input[placeholder*='code' i][placeholder*='bild' i]",
                "input[aria-label*='captcha' i]",
                "input[name*='captcha' i]:not([type='hidden'])",
            ]) {
                if (deepQuery(doc, sel)) { hasMetaCaptchaInput = true; break; }
            }
        }
        // Text-Signale spezifisch für Meta-Plattformen
        const metaCaptchaTexts = [
            "bestätige, dass du kein roboter bist",
            "confirm you're not a robot",
            "hör dir diesen code an","fordere einen neuen code an",
            "gib den code auf der abbildung ein",
            "gib den text auf dem bild ein",
            "type the code shown above","enter the text you see",
        ];
        const hasMetaCaptchaText = metaCaptchaTexts.some(t => allText.includes(t));

        // ── Ebene 2: Pflicht-Explizit-Signal ────────────────────────
        const hasExplicitSignal =
            hasCaptchaResponse ||
            hasIgCheckpoint || hasIgFrame ||
            hasFbSecurity ||
            (hasIgSuspended && (hasMetaCaptchaImg || hasMetaCaptchaText || hasMetaCaptchaInput)) ||
            (hasMetaCaptchaImg && hasMetaCaptchaInput && hasMetaCaptchaText);

        if (!hasExplicitSignal) return null;

        // Auf bekannten Non-CAPTCHA-Pfaden: nur bei starkem Primärsignal melden
        if (isNonCaptchaPage && !hasCaptchaResponse) return null;

        const platform = onIgDomain ? "Instagram" : "Facebook";
        return this.result({
            description: `Text-CAPTCHA (${platform}) – Verzerrte Buchstaben/Zahlen.\n  ➜ Zeichen aus Bild abtippen; Audio-Alternative verfügbar.`,
        });
    }
}


// ════════════════════════════════════════
//  PROVIDER: Generisches Text-CAPTCHA
// ════════════════════════════════════════

class GenericTextCaptchaProvider extends CaptchaProvider {
    constructor() { super("Text-CAPTCHA"); }

    detect() {
        let hasCaptchaImg = false;
        for (const doc of allDocs) {
            for (const sel of ["img[src*='captcha' i]","img[alt*='captcha' i]",
                               "img[id*='captcha' i]","canvas[id*='captcha' i]",
                               "img[src*='challenge' i]","img[src*='verify' i]"]) {
                if (deepQueryVisible(doc, sel)) { hasCaptchaImg = true; break; }
            }
            if (hasCaptchaImg) break;
        }

        let hasCaptchaInput = false;
        for (const doc of allDocs) {
            for (const sel of [
                "input[name*='captcha' i]:not([type='hidden'])",
                "input[id*='captcha' i]:not([type='hidden'])",
                "input[placeholder*='abbildung' i]",
                "input[placeholder*='text from image' i]",
                "input[placeholder*='code' i][placeholder*='bild' i]",
                "input[aria-label*='captcha' i]",
                "#captcha",
            ]) {
                if (deepQuery(doc, sel)) { hasCaptchaInput = true; break; }
            }
            if (hasCaptchaInput) break;
        }

        const strongTextSignals = [
            // Deutsch
            "gib den code auf der abbildung ein",
            "gib den text auf dem bild ein",
            "du kannst den text nicht lesen",
            "bestätige, dass du kein roboter bist",
            "hör dir diesen code an",
            "fordere einen neuen code an",
            // Englisch
            "enter the code shown in the image",
            "type the characters you see",
            "type the text you hear or see",
            "enter text from image",
            "type the code from the image",
            "confirm you're not a robot",
            "enter the text you see",
        ];
        const hasStrongText = strongTextSignals.some(t => allText.includes(t));

        if (!(hasCaptchaImg && hasCaptchaInput) && !(hasStrongText && hasCaptchaInput)) return null;
        return this.result({
            description: "Text-CAPTCHA – Verzerrte Buchstaben/Zahlen im Bild.\n  ➜ Zeichen erkennen und ins Textfeld eintippen.",
        });
    }
}


// ════════════════════════════════════════
//  PROVIDER: Math-CAPTCHA
// ════════════════════════════════════════

class MathCaptchaProvider extends CaptchaProvider {
    constructor() { super("Math-CAPTCHA"); }

    detect() {
        let found = false;
        for (const doc of allDocs) {
            if (deepQuery(doc, "input[id*='math' i], input[name*='math' i], .wpcf7-math-captcha")) {
                found = true; break;
            }
        }
        if (!found) return null;
        return this.result({
            description: "Mathe-CAPTCHA – Rechenaufgabe lösen und Ergebnis eingeben.",
        });
    }
}


// ════════════════════════════════════════
//  ORCHESTRATOR
// ════════════════════════════════════════

class CaptchaScanner {
    constructor() {
        this.providers = [
            new MicrosoftHIPProvider(),        //  1: vor allen anderen!
            new ReCaptchaV3Provider(),         //  2a
            new ReCaptchaV2Provider(),         //  2b
            new HCaptchaProvider(),            //  3
            new TurnstileProvider(),           //  4a
            new CfChallengeProvider(),         //  4b
            // TikTok: drei separate Provider, Priorität Rotate > Slide > 3D Objects
            // Falls mehrere matchen (sollte nicht passieren), gewinnt der erste.
            new TikTokRotateProvider(),        //  5a: stärkste Signale zuerst
            new TikTokSlideProvider(),         //  5b: Slider + Puzzleteil
            new TikTok3DObjectsProvider(),     //  5c: Submit + kein Slider
            new GeeTestProvider(),             //  6
            new MetaTextCaptchaProvider(),     //  7
            new GenericTextCaptchaProvider(),  //  8: Fallback
            new MathCaptchaProvider(),         //  9: immer zusätzlich möglich
        ];
        this.specificTypes = new Set([
            "reCAPTCHA v2","reCAPTCHA v3 / Invisible","hCaptcha",
            "Cloudflare Turnstile","Text-CAPTCHA (Meta)",
            // TikTok-spezifische Namen für GenericText-Ausschluss
            "TikTok CAPTCHA (Rotate)","TikTok CAPTCHA (Puzzle Slide)","TikTok CAPTCHA (3D Objects)",
        ]);
    }

    scan() {
        const results = [];
        let hasRcV3 = false;
        let hasTikTok = false;  // Nur ein TikTok-Typ pro Scan

        for (const provider of this.providers) {
            const name = provider.name;
            if (name === "reCAPTCHA v2" && hasRcV3) continue;
            if (name === "Text-CAPTCHA" && results.some(r => this.specificTypes.has(r.name))) continue;
            // Sobald ein TikTok-Typ gefunden wurde, restliche überspringen
            if (name.startsWith("TikTok CAPTCHA") && hasTikTok) continue;

            const result = provider.detect();
            if (result) {
                results.push(result);
                if (name === "reCAPTCHA v3 / Invisible") hasRcV3  = true;
                if (name.startsWith("TikTok CAPTCHA"))   hasTikTok = true;
            }
        }
        return results;
    }
}

updateGlobals();
const scanner = new CaptchaScanner();
const initialResults = scanner.scan();

if (window.__eventScannerInjected) return initialResults;
window.__eventScannerInjected = true;

const reported = new Set(initialResults.map(r => r.name + (r.solved ? "_solved" : "")));
let debounceTimer = null;

const TARGET_SELECTORS = [
    "iframe", "script", "div[class*='captcha']", "#recaptcha-anchor", 
    ".g-recaptcha", ".h-captcha", ".cf-turnstile", "#challenge-form",
    ".secsdk-captcha-drag-icon", "img", ".captcha-verify-container"
];

function runScanEvent() {
    updateGlobals();
    const results = scanner.scan();
    for (const res of results) {
        const key = res.name + (res.solved ? "_solved" : "");
        if (!reported.has(key)) {
            reported.add(key);
            if (window.__reportCaptchaEvent) {
                window.__reportCaptchaEvent(res);
            }
        }
    }
}

const obsEvent = new MutationObserver((mutations) => {
    let shouldScan = false;
    for (const m of mutations) {
        for (const node of m.addedNodes) {
            if (node.nodeType === 1) {
                if (TARGET_SELECTORS.some(sel => {
                    try { return node.matches(sel) || node.querySelector(sel); } 
                    catch(e) { return false; }
                })) {
                    shouldScan = true;
                    break;
                }
            }
        }
        if (shouldScan) break;
    }
    if (shouldScan) {
        if (debounceTimer) clearTimeout(debounceTimer);
        debounceTimer = setTimeout(runScanEvent, 800);
    }
});

const startObserving = () => {
    obsEvent.observe(document.documentElement, { childList: true, subtree: true });
};

if (document.body) startObserving();
else document.addEventListener("DOMContentLoaded", startObserving);

return initialResults;
})();
"""


# ══════════════════════════════════════════════════════════════════
#  TERMINAL-HILFSFUNKTIONEN
# ══════════════════════════════════════════════════════════════════

def _clear_line():
    sys.stdout.write("\r\033[K")
    sys.stdout.flush()

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")

def _sep(ch: str = "─", n: int = 70) -> None:
    print(ch * n)

def _print_report(captchas: list, url: str, title: str, idx: int, total: int) -> None:
    print()
    _sep("═")
    print(f"  🚨  CAPTCHA ERKANNT  –  {_ts()}")
    print(f"  🗂   Page {idx}/{total}:  {(title or '(kein Titel)')[:52]}")
    print(f"  🌐  {url[:72]}{'...' if len(url) > 72 else ''}")
    _sep("═")
    for i, cap in enumerate(captchas, 1):
        print(f"\n  [{i}]  🤖  {cap['name']}")
        _sep()
        print(f"  📋  {cap['description']}\n")
        if cap.get("solved"):
            print("  ✅  Status: GELÖST")
        elif cap.get("expired"):
            print("  ⏰  Status: TOKEN ABGELAUFEN – bitte neu lösen")
    _sep("═")
    print()


# ══════════════════════════════════════════════════════════════════
#  PLAYWRIGHT-ADAPTER  (primäre Nutzungsform)
# ══════════════════════════════════════════════════════════════════

async def _inject_observer(page) -> None:
    """Injiziert den MutationObserver einmalig pro Page/URL."""
    url = page.url
    if _observer_injected.get(page) == url:
        return
    try:
        await page.evaluate(JS_INJECT_OBSERVERS)
        _observer_injected[page] = url
    except Exception as e:
        print(f"  ⚠️   [Scanner] Fehler bei Observer-Injektion ({url}): {e}") #  # CSP oder Navigation – beim nächsten Zyklus erneut versuchen


async def scan_page(page) -> list[dict]:
    """
    Scannt eine einzelne Playwright-Page auf CAPTCHAs.

    Gibt eine Liste von Erkennungs-Dicts zurück:
        [{"name": str, "solved": bool, "description": str, ...}, ...]

    Beispiel:
        page = await browser.new_page()
        await page.goto("https://example.com")
        results = await master_scanner.scan_page(page)
        for cap in results:
            print(cap["name"], "gelöst:", cap["solved"])
    """
    await _inject_observer(page)
    try:
        result = await page.evaluate(JS_DEEP_SCAN)
        return result if isinstance(result, list) else []
    except Exception as e:
        print(f"  ⚠️   [Scanner] Fehler bei Page-Evaluate (Deep-Scan): {e}")
        return []


async def auto_solve_captcha(cap: dict, page) -> bool:
    """
    Routet das Cap-Dict an den entsprechenden automatischen Solver.

    Import-Strategie: Nutzt den relativen Paketnamen (__package__) oder
    fällt auf lokale Importe zurück.
    """
    import importlib

    name = cap.get("name", "")
    desc = cap.get("description", "").lower()

    print(f"\n  🚀  [AutoSolver] Starte automatischen Lösungs-Prozess für: {name}")

    def _import_solver(module_name: str):
        """Importiert ein Solver-Modul relativ oder über absoluten Pfad."""
        if __package__:
            return importlib.import_module(f"{__package__}.captchas_solver.{module_name}")
        else:
            return importlib.import_module(f"captchas_solver.{module_name}")

    try:
        # 1. TikTok 3D Objects
        if "3d objects" in name.lower() or "identische objekte" in desc:
            solver_mod = _import_solver("object_3d")
            return await solver_mod.solve(page)

        # 2. TikTok Rotate
        elif "rotate" in name.lower():
            solver_mod = _import_solver("rotate")
            return await solver_mod.solve(page)

        # 3. TikTok Puzzle Slide oder GeeTest Slider
        elif "puzzle slide" in name.lower() or "slide" in name.lower() or "geetest" in name.lower():
            solver_mod = _import_solver("slide")
            return await solver_mod.solve(page)

        # 4. reCAPTCHA v2
        elif "recaptcha v2" in name.lower():
            solver_mod = _import_solver("recapctha_v2")
            return await solver_mod.solve(page)

        # 5. Text-Captcha (Meta)
        elif "text-captcha" in name.lower() and ("meta" in name.lower() or "instagram" in name.lower() or "facebook" in name.lower()):
            solver_mod = _import_solver("meta_text")
            return await solver_mod.solve(page)

        else:
            print(f"  ⏭️  [AutoSolver] Kein passender Auto-Solver für '{name}' gefunden.")
            return False

    except Exception as e:
        print(f"  ❌  [AutoSolver] Fehler beim Import/Ausführen des Solvers: {e}")
        return False


async def live_scan(
    context,
    interval: float = SCAN_INTERVAL, # Veraltet, nur für Abwärtskompatibilität
    callback: Optional[Callable] = None,
    auto_solve: bool = True,
) -> None:
    """
    Dauerhafter Event-Driven Scan aller Pages in einem Playwright-BrowserContext.

    Parameter:
        context   – playwright BrowserContext
        interval  – (deprecated)
        callback  – optionale async-Funktion:  async def cb(cap: dict, page) → None
        auto_solve - automatische Lösung
    """
    print(f"\n  🔄  Playwright Event-Driven Scanner v8")
    print("  Alle Pages im Context werden überwacht  –  Strg+C zum Beenden.\n")

    async def handle_captcha(captcha_data: dict, page) -> None:
        name = captcha_data.get("name", "Unknown")
        title = ""
        try:
            title = await page.title()
        except Exception:
            pass

        _print_report([captcha_data], page.url, title, 1, len(context.pages))
        
        if auto_solve and not captcha_data.get("solved"):
            solved = await auto_solve_captcha(captcha_data, page)
            captcha_data["solved"] = solved
            if not solved:
                print(f"\n  ⚠️   [LiveScan] Solver für {name} fehlgeschlagen.")
            else:
                _clear_line()
                print(f"  ✅  [{_ts()}]  Gelöst: {name}")
        
        if callback:
            try:
                await callback(captcha_data, page)
            except Exception:
                pass

    async def __report_captcha_event(source, captcha_data):
        page = source.get("page")
        if not page:
            return
        await handle_captcha(captcha_data, page)

    try:
        await context.expose_binding("__reportCaptchaEvent", __report_captcha_event)
    except Exception as e:
        print(f"  ⚠️   [Scanner] Binding-Fehler (bereits registriert?): {e}") # # Already exposed

    async def setup_page(page):
        try:
            await _inject_observer(page)
            initial_results = await page.evaluate(JS_DEEP_SCAN)
            if isinstance(initial_results, list):
                for captcha_data in initial_results:
                    if isinstance(captcha_data, dict):
                        await handle_captcha(captcha_data, page)
        except Exception as e:
            print(f"  ⚠️   [Scanner] Setup-Fehler für Page {getattr(page, 'url', 'unknown')}: {e}") #

    context.on("page", lambda page: asyncio.create_task(setup_page(page)))
    for page in context.pages:
        asyncio.create_task(setup_page(page))

    # Keep alive
    while True:
        await asyncio.sleep(3600)


def _fetch_cdp_targets(host: str, port: int) -> list[dict]:
    """Liest die offenen CDP-Targets über das JSON-Endpoint aus."""
    endpoint = f"http://{host}:{port}/json/list"
    with urllib.request.urlopen(endpoint, timeout=CDP_RECV_TIMEOUT) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return [target for target in payload if isinstance(target, dict)]


def live_scan_cdp(
    host: str = CDP_HOST,
    port: int = CDP_PORT,
    interval: float = SCAN_INTERVAL,
    callback: Optional[Callable] = None,
) -> None:
    """
    Legacy-Kompatibilitätsmodus für frühere CDP-basierte Aufrufer.

    Hinweis:
        Der eigentliche Deep-Scan benötigt heute Playwright-Page-Objekte und ist
        über `live_scan(context)` event-driven implementiert. Diese Funktion
        verhindert Alt-Aufrufer-Fehler und meldet den Zustand des CDP-Endpunkts.
    """
    print("\n  🔌  Legacy CDP-Scanner aktiviert")
    print("  Für den vollständigen Deep-Scan bitte `live_scan(context)` verwenden.\n")

    last_snapshot: tuple[str, ...] = ()

    while True:
        try:
            targets = _fetch_cdp_targets(host, port)
            page_targets = [
                target for target in targets
                if target.get("type") == "page"
            ]
            snapshot = tuple(
                sorted(
                    str(target.get("url", ""))
                    for target in page_targets
                    if target.get("url")
                )
            )

            if snapshot != last_snapshot:
                _clear_line()
                print(
                    f"  [{_ts()}]  CDP verbunden: {len(page_targets)} Page-Target(s) auf {host}:{port}"
                )
                for idx, url in enumerate(snapshot, 1):
                    print(f"    [{idx}] {url}")
                last_snapshot = snapshot

            if callback:
                try:
                    callback(
                        {
                            "host": host,
                            "port": port,
                            "targets": page_targets,
                            "timestamp": _ts(),
                        }
                    )
                except Exception:
                    pass

        except Exception as exc:
            _clear_line()
            print(f"  [{_ts()}]  CDP-Verbindung fehlgeschlagen ({host}:{port}): {exc}")

        time.sleep(max(interval, 1.0))


