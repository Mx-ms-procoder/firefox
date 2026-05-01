"""
captchas_solver/meta_text.py
════════════════════════════════════════════════════════════════════
Text-CAPTCHA Solver für Instagram und Facebook (Meta-Plattformen).
Vollständig entkoppelt vom Anchoring-System (base_solver).
"""

from __future__ import annotations
import asyncio
import os
import random
import re
from typing import Optional, List

from playwright.async_api import Page

# Relative imports with fallback for direct execution
try:
    from .base_solver import get_nvidia_vision_response, NVIDIA_API_KEY_Qwen
except (ImportError, ValueError):
    import sys
    import os
    # Add current directory to path to support direct execution
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from base_solver import get_nvidia_vision_response, NVIDIA_API_KEY_Qwen  # type: ignore

# ══════════════════════════════════════════════════════════════════
#  KONFIGURATION
# ══════════════════════════════════════════════════════════════════

CAPTCHA_IMAGE_SELECTORS: list[str] = [
    "img#captcha_image", "img[src*='captcha']", "#captcha img",
    "form[action*='captcha'] img", "#captcha_response_wrapper img",
    "img[src*='checkpoint']", "form[action*='checkpoint'] img",
    "img[alt*='captcha' i]", "img[id*='captcha' i]",
    "img[class*='captcha' i]", "canvas[id*='captcha' i]",
]

CAPTCHA_INPUT_SELECTORS: list[str] = [
    "input[name='captcha_response']", "input[name='captcha_token']",
    "input[name='captcha_sid']", "input[id*='captcha' i]:not([type='hidden'])",
    "input[name*='captcha' i]:not([type='hidden'])", "#captcha_input",
    "input[placeholder*='code' i]", "input[placeholder*='text' i]",
]

SUBMIT_SELECTORS: list[str] = [
    "button[type='submit']", "input[type='submit']", "button:has-text('Weiter')",
    "button:has-text('Continue')", "button:has-text('Bestätigen')",
    "button:has-text('Confirm')", "button:has-text('Absenden')",
    "button:has-text('Submit')", "button:has-text('OK')",
    "[data-testid='submit-button']",
]

SUCCESS_SIGNALS: list[str] = ["/home", "/feed", "?next=", "accounts/onetap"]
ERROR_SIGNALS: list[str] = ["incorrect", "wrong", "ungültig", "falsch", "try again", "erneut versuchen"]

class MetaTextSolver:
    """
    Spezialisierter Solver für Meta Text-CAPTCHAs.
    Unabhängig von der AsyncCaptchaSolver-Basisklasse.
    """
    def __init__(self, page: Page):
        self.page = page
        self.api_key = NVIDIA_API_KEY_Qwen

    async def solve(self) -> bool:
        print("\n  🧩  [MetaSolver] Starte Text-CAPTCHA Solver (Meta)…")
        
        url = self.page.url
        platform = "Instagram" if "instagram" in url else "Facebook"
        
        # ── Schritt 1: Full-Page Screenshot ─────────────────────
        image_bytes = await self.page.screenshot(type="png", full_page=True)
        if not image_bytes:
            print("  ❌  [MetaSolver] Screenshot fehlgeschlagen.")
            return False

        # ── Schritt 2: Vision KI befragen ─────────────────────────
        prompt_path = os.path.join(os.path.dirname(__file__), "..", "prompt_meta.txt")
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                prompt = f.read().strip()
        except Exception as e:
            print(f"  ❌  [MetaSolver] Konnte prompt_meta.txt nicht lesen: {e}")
            return False

        prompt = f"Platform: {platform}. " + prompt

        try:
            # Hier nutzen wir temperature=0/top_p=1 für maximale Präzision
            raw_response = await get_nvidia_vision_response(
                image_bytes=image_bytes,
                prompt=prompt,
                api_key=self.api_key,
                temperature=0.0,
                top_p=1.0
            )
        except Exception as e:
            print(f"  ❌  [MetaSolver] API-Fehler: {e}")
            return False

        # ── Schritt 3: Code extrahieren ───────────────────────────
        captcha_code = self._extract_code(raw_response)
        if not captcha_code:
            print("  ⚠️   [MetaSolver] Kein Code aus Antwort extrahierbar.")
            return False

        print(f"  ✅  [MetaSolver] Extrahierter Code: '{captcha_code}'")

        # ── Schritt 4: Eingabefeld finden & Tippen ─────────────────
        input_sel = await self._find_selector(CAPTCHA_INPUT_SELECTORS)
        if not input_sel:
            print("  ❌  [MetaSolver] Kein Eingabefeld gefunden.")
            return False

        before_url = self.page.url
        await self._human_type(input_sel, captcha_code)
        await asyncio.sleep(random.uniform(0.5, 1.2))

        # ── Schritt 5: Senden & Erfolg prüfen ─────────────────────
        await self._submit_form()
        await asyncio.sleep(3) # Warten auf Load

        if await self._check_success(before_url):
            print(f"  🎉  [MetaSolver] CAPTCHA gelöst!")
            return True
        else:
            print(f"  ❌  [MetaSolver] Fehlgeschlagen. Übergebe an Agenten...")
            return False

    async def _find_selector(self, selectors: List[str]) -> Optional[str]:
        for sel in selectors:
            try:
                elem = self.page.locator(sel).first
                if await elem.is_visible():
                    return sel
            except: continue
        return None

    def _extract_code(self, raw_response: str) -> str:
        """Spezialisierte Extraktion für Meta-Codes."""
        match = re.search(r'[`"\']?([A-Za-z0-9]{4,10})[`"\']?', raw_response)
        if match: return match.group(1).strip()
        cleaned = re.sub(r'[^A-Za-z0-9]', '', raw_response)
        return cleaned[:10]

    async def _human_type(self, selector: str, text: str):
        """Simuliert menschliches Tippen."""
        await self.page.fill(selector, "")
        for char in text:
            await self.page.keyboard.type(char)
            await asyncio.sleep(random.uniform(0.08, 0.25))

    async def _submit_form(self):
        submit_sel = await self._find_selector(SUBMIT_SELECTORS)
        if submit_sel:
            await self.page.click(submit_sel)
        else:
            await self.page.keyboard.press("Enter")

    async def _check_success(self, before_url: str) -> bool:
        current_url = self.page.url
        if current_url != before_url:
            if any(sig in current_url for sig in SUCCESS_SIGNALS): return True
            if "captcha" not in current_url.lower() and "checkpoint" not in current_url.lower(): return True
        
        try:
            body_text = (await self.page.inner_text("body")).lower()
            if any(sig in body_text for sig in ERROR_SIGNALS): return False
        except: pass

        input_sel = await self._find_selector(CAPTCHA_INPUT_SELECTORS)
        return input_sel is None

async def solve(page: Page) -> bool:
    """Kompatibilitäts-Wrapper für den alten Aufruf."""
    solver = MetaTextSolver(page)
    return await solver.solve()