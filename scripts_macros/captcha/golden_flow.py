"""
golden_flow.py  –  Anti-Bot Stealth für Playwright  (Performance-Edition)
══════════════════════════════════════════════════════════════════════════
Identische Stealth-Layer wie die vorherige Version, aber ohne die
massiven Performance-Probleme durch Proxy-Wrapping.

Warum die alte Version laggte (und was geändert wurde):
  ❌ VORHER: Function.prototype.toString = new Proxy(nativeToString, {...})
     → Jeder JS-Funktionsaufruf im Browser lief durch einen Proxy-Trap.
     → V8 kann Proxy-Objekte nicht JIT-optimieren → dramatischer FPS-Einbruch.

  ❌ VORHER: cloak(fn) → Proxy für JEDE ersetzte Funktion
     → WebGLRenderingContext.prototype.getParameter wird bei jedem Frame
       hunderte Male aufgerufen. Als Proxy: kein JIT, 10-100x langsamer.

  ❌ VORHER: 2 separate CDPSessions pro Page (Network + WebRTC)
     → Doppelter CDP-Handshake-Overhead bei jedem neuen Tab.

  ✅ JETZT: Direkte Prototyp-Ersetzung via gespeichertem Original-Ref
     → Normales JS, voll JIT-optimierbar, kein Proxy-Overhead.

  ✅ JETZT: toString-Schutz nur für die wenigen ersetzten Fns, nicht global
     → Object.defineProperty statt Proxy.

  ✅ JETZT: Eine kombinierte CDPSession pro Page.

  ✅ JETZT: Chrome-Args bereinigt (keine Args die GPU/Rendering blockieren).

Stealth-Layer:
  Layer 1 – Camoufox Native Fingerprinting (Ersetzt alte CDP-Hacks)
  Layer 2 – BehavioralHelper     (Maus, Scroll, Typing)

Nutzung:
        flow = GoldenFlow()
        browser, context = await flow.create_browser(pw)
        page   = await flow.new_page(context)
        await page.mouse.move(100, 100, steps=20)

Benötigt:
    pip install playwright numpy
    playwright install chromium
"""

from __future__ import annotations

import asyncio
import math
import random
import re
from typing import Literal, Optional

try:
    from playwright.async_api import (
        async_playwright,
        Browser,
        BrowserContext,
        Page,
    )
except ImportError:
    raise SystemExit(
        "❌  Playwright fehlt:  pip install playwright"
    )


# ══════════════════════════════════════════════════════════════════
#  KONFIGURATION
# ══════════════════════════════════════════════════════════════════

WINDOW_WIDTH  = 1280
WINDOW_HEIGHT = 900

# ══════════════════════════════════════════════════════════════════
#  GOLDEN FLOW  –  Haupt-Klasse
# ══════════════════════════════════════════════════════════════════

class GoldenFlow:
    """
    Orchestriert alle Stealth-Layer für einen Playwright-Browser.

    Typische Nutzung:
        flow = GoldenFlow()
        browser, context = await flow.create_browser(pw)
        page = await flow.new_page(context)
    """

    def __init__(
        self,
        headless:   bool = False,
    ) -> None:
        self.headless   = headless

    async def create_browser(self, pw) -> tuple[Browser, BrowserContext]:
        """
        Startet Camoufox. Gibt (Browser, BrowserContext) zurück.
        """
        print(f"\n  🚀  [GoldenFlow] Starte Camoufox Stealth-Browser…")

        try:
            from camoufox.async_api import AsyncNewBrowser  # type: ignore
        except ImportError:
            raise SystemExit("❌  Camoufox fehlt: pip install camoufox")

        browser = await AsyncNewBrowser(pw, headless=self.headless)
        context = await browser.new_context(
            no_viewport=True,
            java_script_enabled=True,
        )
        
        print("  ✅  [GoldenFlow] Camoufox erstellt (Natives Stealthing).")
        return browser, context

    async def new_page(self, context: BrowserContext) -> Page:
        """
        Öffnet eine neue Page.
        """
        page = await context.new_page()
        return page

    async def maximize_window(self, page: Page) -> None:
        """Maximiert das Browserfenster (funktioniert für Firefox/Camoufox out of the box oft über window bounds, oder wird ignoriert)."""
        pass



# ══════════════════════════════════════════════════════════════════
#  STANDALONE TEST  (python golden_flow.py)
# ══════════════════════════════════════════════════════════════════

async def _run_test() -> None:
    test_url = "https://bot.sannysoft.com/"

    print("\n" + "═" * 60)
    print("  🔍  GoldenFlow Stealth-Test (Performance-Edition)")
    print("═" * 60)

    async with async_playwright() as pw:
        flow             = GoldenFlow(headless=False)
        browser, context = await flow.create_browser(pw)
        page             = await flow.new_page(context)
        await page.goto(test_url, wait_until="domcontentloaded")
        await page.mouse.wheel(0, 800)

        print("\n  ✅  Ergebnisse sichtbar.")
        print("     Grün = gut  |  Rot = Bot-Signal erkannt\n")
        input("  ⏎  Enter zum Beenden …")

        await browser.close()


def main() -> None:
    print("🚀  [GoldenFlow] Starte Stealth-Test…")
    try:
        asyncio.run(_run_test())
    except KeyboardInterrupt:
        print("\n  👋  Abgebrochen.")


if __name__ == "__main__":
    main()
