"""
groq_client.py — Jarvis Groq Integration
Ultra-fast inference via Groq's LPU hardware (sub-500ms typical TTFT).

Primary model  : llama-3.3-70b-versatile  (best quality + speed balance)
Fast model     : llama-3.1-8b-instant      (< 200 ms, ideal for quick replies)
Fallback       : gemini-flash-lite-latest  (if Groq key not set or quota hit)

Supports multiple Groq API keys via key_manager round-robin rotation.
"""

from __future__ import annotations

from core.secrets import KEYS_FILE  # keys/tokens are encrypted at rest (DPAPI)
from core.paths import DATA_DIR  # owner data lives apart from the program
import json
import logging
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger("groq_client")


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = _base_dir()
API_CONFIG_PATH = KEYS_FILE

# Groq model aliases
MODEL_FAST    = "llama-3.1-8b-instant"      # ~150 ms TTFT
MODEL_QUALITY = "llama-3.3-70b-versatile"   # ~400 ms TTFT — full reasoning

try:
    from key_manager import get_key as _km_get, mark_rate_limited as _km_rl, is_available as _km_avail
    _KM_OK = True
except ImportError:
    _KM_OK = False


def _get_groq_key() -> str:
    """Return a Groq API key using round-robin rotation when multiple keys are configured."""
    if _KM_OK:
        return _km_get("groq") or ""
    # Legacy fallback — read single key directly
    try:
        with API_CONFIG_PATH.open("r", encoding="utf-8") as f:
            return json.load(f).get("groq_api_key", "").strip()
    except Exception:
        return ""


class GroqClient:
    """Thin wrapper around Groq's OpenAI-compatible REST API."""

    BASE_URL = "https://api.groq.com/openai/v1"

    def __init__(self) -> None:
        self._key: str = ""

    def is_available(self) -> bool:
        if _KM_OK:
            return _km_avail("groq")
        self._key = _get_groq_key()
        return bool(self._key)

    def chat(
        self,
        prompt: str,
        system: str = "You are Jarvis, a concise, helpful desktop assistant. Reply naturally and briefly.",
        model: str = MODEL_QUALITY,
        temperature: float = 0.6,
        max_tokens: int = 1024,
        timeout: float = 10.0,
    ) -> str:
        import urllib.request, urllib.error

        self._key = _get_groq_key()
        if not self._key:
            raise RuntimeError("Groq API key not configured.")

        payload = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens":  max_tokens,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.BASE_URL}/chat/completions",
            data=payload,
            headers={
                "Content-Type":  "application/json",
                "Authorization": f"Bearer {self._key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code in (429, 413) and _KM_OK and self._key:
                _km_rl("groq", self._key)
            raise RuntimeError(f"Groq HTTP {e.code}: {body[:300]}") from e
        except Exception as exc:
            raise RuntimeError(f"Groq request failed: {exc}") from exc

    def fast_reply(self, prompt: str, system: str = "") -> str:
        """Ultra-fast reply using the 8B instant model (< 200 ms typical)."""
        sys_msg = system or (
            "You are Jarvis, a concise AI assistant. "
            "Give a very short, direct answer in 1-2 sentences max."
        )
        return self.chat(prompt, system=sys_msg, model=MODEL_FAST, max_tokens=256)

    def quality_reply(self, prompt: str, system: str = "") -> str:
        """Higher-quality reply using the 70B model."""
        sys_msg = system or (
            "You are Jarvis, a helpful desktop assistant. "
            "Reply naturally, accurately, and briefly."
        )
        return self.chat(prompt, system=sys_msg, model=MODEL_QUALITY, max_tokens=1024)

    def fetch_tech_news(self, n: int = 5) -> str:
        """Fetch and summarise the top-N tech news headlines."""
        try:
            from actions.web_search import _ddg_search
            results = _ddg_search("latest technology AI news today 2025", max_results=n)
        except Exception as exc:
            logger.warning(f"[Groq] DDG news fetch failed: {exc}")
            results = []

        if not results:
            return "I couldn't fetch tech news right now, sir."

        snippets = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:120]}"
            for r in results[:n]
        )
        prompt = (
            f"Here are {len(results)} tech headlines:\n{snippets}\n\n"
            "Summarise the 3 most interesting stories in 3 short spoken sentences "
            "as Jarvis would say them aloud. No bullet points, just natural conversational speech."
        )
        try:
            return self.fast_reply(prompt, system=(
                "You are Jarvis, a crisp AI assistant. "
                "Deliver a spoken-word tech briefing — punchy and conversational. "
                "Speak in first person as if talking to your boss."
            ))
        except Exception as exc:
            logger.warning(f"[Groq] News summarise failed: {exc}")
            titles = "; ".join(r.get("title", "") for r in results[:3] if r.get("title"))
            return f"Here are today's top tech stories: {titles}." if titles else "No news available right now, sir."


# Singleton
groq_client = GroqClient()
