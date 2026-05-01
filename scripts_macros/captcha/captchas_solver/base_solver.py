"""
captchas_solver/base_solver.py
════════════════════════════════════════════════════════════════════
Basis-Klasse für alle CAPTCHA-Solver.
Zentralisiert Vision-API-Anfragen und gemeinsame Hilfsfunktionen.
"""

from __future__ import annotations
import asyncio
import base64
import json
import os
import random
import re
from typing import Optional, List, Dict, Any

import requests
from playwright.async_api import Page, Locator

# ══════════════════════════════════════════════════════════════════
#  KONFIGURATION (Standardwerte, können überschrieben werden)
# ══════════════════════════════════════════════════════════════════

try:
    from .api_config import NVIDIA_API_KEY_Qwen, NVIDIA_API_KEY_Gemma
except ImportError:
    NVIDIA_API_KEY_Qwen = NVIDIA_API_KEY_Gemma = ""

if not NVIDIA_API_KEY_Qwen or "DEIN_" in NVIDIA_API_KEY_Qwen:
    NVIDIA_API_KEY_Qwen = os.environ.get("NVIDIA_API_KEY_Qwen", os.environ.get("NVIDIA_API_KEY", ""))
if not NVIDIA_API_KEY_Gemma or "DEIN_" in NVIDIA_API_KEY_Gemma:
    NVIDIA_API_KEY_Gemma = os.environ.get("NVIDIA_API_KEY_Gemma", os.environ.get("NVIDIA_API_KEY", ""))

NVIDIA_INVOKE_URL: str = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_MODEL: str = "qwen/qwen3.5-122b-a10b" # Default Vision Model

class AsyncCaptchaSolver:
    """
    Abstrakte Basisklasse für CAPTCHA-Solver.
    """
    def __init__(self, page: Page, model: Optional[str] = None):
        self.page = page
        self.model = model or NVIDIA_MODEL
        # Standard: Benutze Qwen's Key, es sei denn Gemma ist das Modell
        if "gemma" in self.model.lower():
            self.api_key = NVIDIA_API_KEY_Gemma
        else:
            self.api_key = NVIDIA_API_KEY_Qwen
        
        if not self.api_key:
            print(f"  ⚠️   [BaseSolver] Warnung: API_KEY für Modell '{self.model}' nicht gesetzt.")

    async def solve(self) -> bool:
        """Muss von Unterklassen implementiert werden."""
        raise NotImplementedError("Subclasses must implement solve()")

    # ── Vision API Hilfsfunktionen ──────────────────────────────────

    # ── Vision API Hilfsfunktionen ──────────────────────────────────
    
    async def get_vision_response(
        self, 
        image_bytes: bytes, 
        prompt: str, 
        temperature: float = 0.0,
        top_p: float = 1.0
    ) -> str:
        """Sendet Bild an Nvidia Vision API und gibt Antwort zurück."""
        return await get_nvidia_vision_response(
            image_bytes=image_bytes,
            prompt=prompt,
            api_key=self.api_key,
            model=self.model,
            temperature=temperature,
            top_p=top_p
        )

    # ── Playwright Hilfsfunktionen ──────────────────────────────────

    async def find_selector(self, selectors: List[str]) -> Optional[str]:
        """Gibt den ersten sichtbaren Selektor zurück."""
        for sel in selectors:
            try:
                elem = self.page.locator(sel).first
                if await elem.is_visible():
                    return sel
            except: continue
        return None

    async def screenshot_fullpage(self) -> bytes:
        """Macht einen Full-Page Screenshot."""
        return await self.page.screenshot(type="png", full_page=True)
    
    def extract_percentage(self, text: str) -> Optional[float]:
        """Extrahiert einen Prozentwert (0-100) aus einem Text."""
        match = re.search(r'(\d+(?:\.\d+)?)\s*%', text)
        if match:
            val = float(match.group(1))
            return max(0.0, min(100.0, val))
        
        # Fallback: Einfach die erste Zahl
        match = re.search(r'(\d+(?:\.\d+)?)', text)
        if match:
            val = float(match.group(1))
            if 0 <= val <= 100:
                return val
        return None

    def extract_target_x(self, text: str) -> Optional[int]:
        """Extrahiert Target_X Koordinate aus Text (Format: 'Target_X: 123')."""
        match = re.search(r'Target_X:\s*(\d+)', text, re.IGNORECASE)
        if match:
            return int(match.group(1))
        # Fallback: Findet die letzte Zahl im Text, falls das Modell sich nicht ans Format hielt
        matches = re.findall(r'\b\d+\b', text)
        if matches:
            return int(matches[-1])
        return None

async def get_nvidia_vision_response(
    image_bytes: bytes, 
    prompt: str, 
    api_key: str,
    model: str = NVIDIA_MODEL,
    temperature: float = 0.0,
    top_p: float = 1.0
) -> str:
    """Standalone Hilfsfunktion für Nvidia Vision-Anfragen."""
    if not api_key:
        raise EnvironmentError("NVIDIA_API_KEY fehlt.")

    b64 = base64.b64encode(image_bytes).decode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": 16384,
        "temperature": temperature,
        "top_p": top_p,
        "stream": True,
        "chat_template_kwargs": {"enable_thinking": True},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }
        ],
    }

    def _sync_fetch():
        response = requests.post(NVIDIA_INVOKE_URL, headers=headers, json=payload, stream=True, timeout=30)
        response.raise_for_status()

        full_text = ""
        for line in response.iter_lines():
            if not line: continue
            decoded = line.decode("utf-8")
            if decoded.startswith("data:"):
                decoded = decoded[5:].strip()
            if decoded == "[DONE]": break
            try:
                chunk = json.loads(decoded)
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                
                # Check for standard content or reasoning_content
                content = delta.get("content", "")
                reasoning = delta.get("reasoning_content", "")
                
                if content: full_text += content
                if reasoning: full_text += reasoning
            except: continue
        return full_text

    # Ausführung im Executor, da 'requests' blockierend ist
    loop = asyncio.get_event_loop()
    full_text = await loop.run_in_executor(None, _sync_fetch)

    # Strip any <think> tags if they exist to get the cleaner output
    full_text = re.sub(r'<think>.*?</think>', '', full_text, flags=re.DOTALL)
    return full_text.strip()
