"""The Forge's thinking model.

Order of preference:
  1. Claude Opus 5.5 (if config/api_keys.json has "anthropic_api_key" and the
     `anthropic` package is installed: pip install anthropic). Adaptive thinking,
     effort "high", streaming, server-side refusal fallback.
  2. Gemini 3.1 Pro / Gemini Pro (needs a paid Gemini plan: the free tier has no Pro quota).
  3. The newest Gemini Flash models, run at the HIGHEST thinking level.
  A quota error (429) skips straight to the next model; "busy" (503) retries briefly first.

The fast voice model is never used for building skills.
"""

from __future__ import annotations

from core.secrets import KEYS_FILE  # keys/tokens are encrypted at rest (DPAPI)
from core.paths import DATA_DIR  # owner data lives apart from the program
import json
import logging
import time
from pathlib import Path

log = logging.getLogger("skill_forge.brain")

BASE_DIR = Path(__file__).resolve().parents[2]
CLAUDE_MODEL = "claude-opus-5-5"
GEMINI_MODELS = (
    "gemini-3.1-pro-preview", "gemini-pro-latest",                  # deepest; paid plans
    "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash",     # newest thinking Flash
    "gemini-3.5-flash", "gemini-3-flash-preview",
)
_skip_until: dict[str, float] = {}  # models that hit their quota / are overloaded are skipped for a while
_last_good: list[str] = []          # the model that worked most recently is tried first


def _keys() -> dict:
    try:
        return json.loads(KEYS_FILE.read_text(encoding="utf-8-sig")) or {}
    except Exception:
        return {}


def available_models() -> list[str]:
    keys = _keys()
    out = []
    if (keys.get("anthropic_api_key") or "").strip():
        try:
            import anthropic  # noqa: F401
            out.append(CLAUDE_MODEL)
        except Exception:
            pass
    if (keys.get("gemini_api_key") or "").strip():
        now = time.time()
        ready = [m for m in GEMINI_MODELS if _skip_until.get(m, 0) < now]
        if not ready:  # everything is cooling down: try them all rather than fail
            ready = list(GEMINI_MODELS)
        out.extend(sorted(ready, key=lambda m: 0 if m in _last_good else 1))
    return out


def _claude(system: str, prompt: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=_keys()["anthropic_api_key"].strip())
    with client.beta.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "high"},
        system=system,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError("Claude declined this request.")
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    if not text:
        raise RuntimeError("Claude returned no text.")
    return text


def _gemini(model: str, system: str, prompt: str, max_tokens: int, json_mode: bool) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=_keys()["gemini_api_key"].strip())
    cfg = types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=max_tokens,
        response_mime_type="application/json" if json_mode else None,
        thinking_config=types.ThinkingConfig(thinking_level="HIGH"),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    last = None
    for attempt in range(2):
        try:
            resp = client.models.generate_content(model=model, contents=prompt, config=cfg)
            text = (resp.text or "").strip()
            if text:
                return text
            raise RuntimeError("empty reply")
        except Exception as exc:
            last = exc
            msg = str(exc)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                _skip_until[model] = time.time() + 3600  # out of quota (free tier resets daily): skip for an hour
                raise
            if "thinking" in msg.lower() and "level" in msg.lower():
                cfg.thinking_config = None              # model without thinking levels
                continue
            busy = any(c in msg for c in ("503", "UNAVAILABLE", "overloaded", "empty reply", "disconnected"))
            if not busy:
                raise
            if attempt == 1:
                _skip_until[model] = time.time() + 180   # overloaded: give it 3 minutes
                raise
            time.sleep(4)
    raise RuntimeError(f"{model} busy: {last}")


def think(system: str, prompt: str, *, json_mode: bool = False, max_tokens: int = 32000) -> tuple[str, str]:
    """Return (text, model_used). Tries the strongest available model first."""
    errors = []
    for model in available_models():
        try:
            if model == CLAUDE_MODEL:
                text = _claude(system, prompt + ("\n\nReply with JSON only." if json_mode else ""), max_tokens)
            else:
                text = _gemini(model, system, prompt, max_tokens, json_mode)
            if model not in _last_good:
                _last_good[:] = [model]
            return text, model
        except Exception as exc:
            errors.append(f"{model}: {str(exc)[:160]}")
            log.warning(f"Skill Forge: {model} failed, trying next ({str(exc)[:100]})")
    raise RuntimeError("No thinking model available. " + " | ".join(errors or ["Add a Gemini key."]))


def think_json(system: str, prompt: str, *, max_tokens: int = 16000) -> tuple[dict, str]:
    text, model = think(system, prompt, json_mode=True, max_tokens=max_tokens)
    clean = text.strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[1] if "\n" in clean else clean
        clean = clean.rsplit("```", 1)[0]
    start, end = clean.find("{"), clean.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"{model} did not return JSON")
    return json.loads(clean[start:end + 1]), model


def extract_code(text: str, tag: str = "python") -> str:
    """Pull the first fenced code block out of a model reply."""
    fence = f"```{tag}"
    if fence in text:
        body = text.split(fence, 1)[1]
        return body.split("```", 1)[0].strip("\n") + "\n"
    if "```" in text:
        body = text.split("```", 1)[1]
        body = body.split("\n", 1)[1] if "\n" in body else body
        return body.split("```", 1)[0].strip("\n") + "\n"
    return text.strip() + "\n"
