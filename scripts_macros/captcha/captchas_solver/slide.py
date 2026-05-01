"""
captchas_solver/slide.py
════════════════════════════════════════════════════════════════════
Solver für Slide-CAPTCHAs (Puzzle-basiert).
Nutzt Vision-KI zur Erkennung des Ziel-Slots und Playwright zum Schieben.
"""

from __future__ import annotations
import asyncio
import os
import random
from typing import Optional

from playwright.async_api import Page

# Relative imports with fallback for direct execution
try:
    from .base_solver import AsyncCaptchaSolver
except (ImportError, ValueError):
    import sys
    import os
    # Add current and parent directory to path to support direct execution
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from base_solver import AsyncCaptchaSolver  # type: ignore
# ══════════════════════════════════════════════════════════════════
#  KONFIGURATION
# ══════════════════════════════════════════════════════════════════

SLIDE_KNOB_SELECTORS = [
    ".secsdk-captcha-drag-icon",     # TikTok
    ".geetest_slider_button",        # GeeTest
    ".nc_iconfont.btn_slide",        # Alibaba
    ".slide-to-unlock-handle",       # Generisch
    "[class*='slider-handle' i]",
    "[class*='captcha-drag' i]",
]

SLIDE_TRACK_SELECTORS = [
    ".captcha_verify_slide--wrapper",# TikTok
    ".geetest_slider",               # GeeTest
    ".nc-lang-cnt",                  # Alibaba
    ".slide-to-unlock-bg",           # Generisch
    "[class*='slider-track' i]",
    "[class*='captcha-track' i]",
]

class SlideSolver(AsyncCaptchaSolver):
    """
    Solver für Puzzle-Slide-CAPTCHAs.
    """
    def __init__(self, page: Page):
        super().__init__(page)

    async def solve(self) -> bool:
        print("\n  🧩  [SlideSolver] Starte Slide-CAPTCHA Solver…")

        # ── Schritt 1: Elemente finden ────────────────────────────
        knob_sel = await self.find_selector(SLIDE_KNOB_SELECTORS)
        track_sel = await self.find_selector(SLIDE_TRACK_SELECTORS)

        if not knob_sel or not track_sel:
            print("  ❌  [SlideSolver] Slider-Elemente nicht gefunden.")
            return False

        knob = self.page.locator(knob_sel).first
        track = self.page.locator(track_sel).first

        k_bbox = await knob.bounding_box()
        t_bbox = await track.bounding_box()

        if not k_bbox or not t_bbox:
            print("  ❌  [SlideSolver] BoundingBox konnte nicht ermittelt werden.")
            return False

        track_x_start = int(t_bbox["x"])
        track_width = int(t_bbox["width"])
        
        print(f"  📍  [SlideSolver] Track Start: {track_x_start}px, Breite: {track_width}px")

        # ── Schritt 2: Full-Page Screenshot & KI-Analyse ──────────
        image_bytes = await self.screenshot_fullpage()
        prompt_path = os.path.join(os.path.dirname(__file__), "..", "prompt_slide.txt")
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                prompt = f.read().strip()
        except Exception as e:
            print(f"  ❌  [SlideSolver] Konnte prompt_slide.txt nicht lesen: {e}")
            return False

        # Platzhalter im Prompt durch echte Koordinaten ersetzen
        track_x_end = track_x_start + track_width
        prompt = prompt.replace("[FÜGE HIER track_x_start EIN]", str(track_x_start))
        prompt = prompt.replace("[FÜGE HIER track_x_start + track_width EIN]", str(track_x_end))

        try:
            raw_response = await self.get_vision_response(image_bytes, prompt)
            print(f"  🤖  [SlideSolver] KI-Antwort: '{raw_response}'")
            target_x = self.extract_target_x(raw_response)
        except Exception as e:
            print(f"  ❌  [SlideSolver] KI-Fehler: {e}")
            return False

        if target_x is None:
            print("  ⚠️   [SlideSolver] Konnte keinen Target_X Wert extrahieren.")
            return False

        if not (track_x_start <= target_x <= track_x_end):
            print(f"  ⚠️   [SlideSolver] Target_X ({target_x}) außerhalb der Schiene ({track_x_start} - {track_x_end}).")
            return False

        print(f"  🎯  [SlideSolver] Ziel-Position extrahiert (Target_X): {target_x}")

        # ── Schritt 3/4: Ausführen der Schiebebewegung ────────────
        print(f"  🖱️   [SlideSolver] Ziehe Slider nach X={target_x}…")
        
        start_x = k_bbox["x"] + k_bbox["width"] / 2
        start_y = k_bbox["y"] + k_bbox["height"] / 2

        await self.page.mouse.move(start_x, start_y)
        await self.page.mouse.down()
        await asyncio.sleep(random.uniform(0.2, 0.4))

        # Menschliche Mausbewegung (Drag) mit Playwright + Camoufox native stealth
        await self.page.mouse.move(
            float(target_x),
            start_y,
            steps=random.randint(30, 50)
        )

        await asyncio.sleep(random.uniform(0.3, 0.6))
        await self.page.mouse.up()

        # Kurzes Warten auf Validierung
        await asyncio.sleep(2)
        
        # ── Schritt 5: Erfolgskontrolle ───────────────────────────
        # Einfache Heuristik: Ist das Element nach dem Versuch noch da?
        knob_still_visible = await self.find_selector(SLIDE_KNOB_SELECTORS)
        if not knob_still_visible:
            print("  🎉  [SlideSolver] CAPTCHA offenbar gelöst!")
            return True
        else:
            print(f"  ❌  [SlideSolver] Versuch fehlgeschlagen. Übergebe an Agenten...")
            return False

async def solve(page: Page) -> bool:
    solver = SlideSolver(page)
    return await solver.solve()
