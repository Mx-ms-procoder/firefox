"""
captchas_solver/rotate.py
════════════════════════════════════════════════════════════════════
Solver für Rotations-CAPTCHAs (Kreis ausrichten).
Nutzt Vision-KI zur Schätzung des Winkels und Playwright zum Schieben.
Mathematischer Ansatz: Schiebe-Regler (0=0°, Max=359°).
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

ROTATE_KNOB_SELECTORS = [
    ".secsdk-captcha-drag-icon",     # TikTok
    ".geetest_slider_button",        # GeeTest
    ".slide-to-unlock-handle",       # Generisch
    "[class*='slider-handle' i]",
    "[class*='captcha-drag' i]",
]

ROTATE_TRACK_SELECTORS = [
    ".captcha_verify_slide--wrapper",# TikTok
    ".geetest_slider",               # GeeTest
    ".slide-to-unlock-bg",           # Generisch
    "[class*='slider-track' i]",
    "[class*='captcha-track' i]",
]

class RotateSolver(AsyncCaptchaSolver):
    """
    Solver für Rotations-CAPTCHAs.
    """
    def __init__(self, page: Page):
        super().__init__(page)

    async def solve(self) -> bool:
        print("\n  🧩  [RotateSolver] Starte Rotate-CAPTCHA Solver…")

        # ── Schritt 1: Elemente finden ────────────────────────────
        knob_sel = await self.find_selector(ROTATE_KNOB_SELECTORS)
        track_sel = await self.find_selector(ROTATE_TRACK_SELECTORS)

        if not knob_sel or not track_sel:
            print("  ❌  [RotateSolver] Slider-Elemente nicht gefunden.")
            return False

        knob = self.page.locator(knob_sel).first
        track = self.page.locator(track_sel).first

        k_bbox = await knob.bounding_box()
        t_bbox = await track.bounding_box()

        if not k_bbox or not t_bbox:
            print("  ❌  [RotateSolver] BoundingBox konnte nicht ermittelt werden.")
            return False

        track_x_start = int(t_bbox["x"])
        track_width = int(t_bbox["width"])

        print(f"  📌  [RotateSolver] Track Start: {track_x_start}px, Breite: {track_width}px")

        # ── Schritt 2: Full-Page Screenshot & KI-Analyse ──────────
        image_bytes = await self.screenshot_fullpage()
        prompt_path = os.path.join(os.path.dirname(__file__), "..", "prompt_rotate.txt")
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                prompt = f.read().strip()
        except Exception as e:
            print(f"  ❌  [RotateSolver] Konnte prompt_rotate.txt nicht lesen: {e}")
            return False

        # Platzhalter im Prompt durch echte Koordinaten ersetzen
        track_x_end = track_x_start + track_width
        prompt = prompt.replace("[FÜGE HIER track_x_start EIN]", str(track_x_start))
        prompt = prompt.replace("[FÜGE HIER track_x_start + track_width EIN]", str(track_x_end))

        try:
            raw_response = await self.get_vision_response(image_bytes, prompt)
            print(f"  🤖  [RotateSolver] KI-Antwort: '{raw_response}'")
            target_x = self.extract_target_x(raw_response)
        except Exception as e:
            print(f"  ❌  [RotateSolver] KI-Fehler: {e}")
            return False

        if target_x is None:
            print("  ⚠️   [RotateSolver] Konnte keinen Target_X Wert extrahieren.")
            return False

        if not (track_x_start <= target_x <= track_x_end):
            print(f"  ⚠️   [RotateSolver] Target_X ({target_x}) außerhalb der Schiene ({track_x_start} - {track_x_end}).")
            return False

        print(f"  🎯  [RotateSolver] Ziel-Position extrahiert (Target_X): {target_x}")

        # ── Schritt 3/4: Ausführen der Schiebebewegung ────────────
        print(f"  🖱️   [RotateSolver] Ziehe Slider nach X={target_x}…")
        
        start_x = k_bbox["x"] + k_bbox["width"] / 2
        start_y = k_bbox["y"] + k_bbox["height"] / 2

        await self.page.mouse.move(start_x, start_y)
        await self.page.mouse.down()
        await asyncio.sleep(random.uniform(0.1, 0.3))

        # Menschliche Mausbewegung (Drag) mit Playwright + Camoufox native stealth
        await self.page.mouse.move(
            float(target_x),
            start_y,
            steps=random.randint(40, 60)
        )

        await asyncio.sleep(random.uniform(0.5, 1.0))
        await self.page.mouse.up()

        # Kurzes Warten auf validierung
        await asyncio.sleep(2)
        
        # ── Schritt 5: Erfolgskontrolle ───────────────────────────
        knob_still_visible = await self.find_selector(ROTATE_KNOB_SELECTORS)
        if not knob_still_visible:
            print("  🎉  [RotateSolver] CAPTCHA offenbar gelöst!")
            return True
        else:
            print(f"  ❌  [RotateSolver] Versuch fehlgeschlagen. Übergebe an Agenten zur Retry-Prüfung…")
            return False

async def solve(page: Page) -> bool:
    solver = RotateSolver(page)
    return await solver.solve()
