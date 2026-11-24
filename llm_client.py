from core.secrets import KEYS_FILE  # keys/tokens are encrypted at rest (DPAPI)
from core.paths import DATA_DIR  # owner data lives apart from the program
import json
import logging
import requests
from pathlib import Path
from typing import Optional
from or_client import client as openrouter_client

logger = logging.getLogger("llm_client")

def _get_base_dir() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR = _get_base_dir()
SETTINGS_PATH = DATA_DIR / "config" / "app_settings.json"
API_KEYS_PATH = KEYS_FILE
GEMINI_TEXT_MODEL = "gemini-flash-lite-latest"


def _api_keys() -> dict:
    try:
        with API_KEYS_PATH.open("r", encoding="utf-8-sig") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _gemini_generate(messages: list[dict], max_tokens: int, temperature: float,
                     json_mode: bool = False, image: Optional[tuple[str, str]] = None) -> str:
    """Text (and optional image) completion through Gemini."""
    import base64
    from google import genai
    from google.genai import types

    key = (_api_keys().get("gemini_api_key") or "").strip()
    if not key:
        raise RuntimeError("Gemini key missing")
    system = "\n".join(m["content"] for m in messages if m.get("role") == "system" and isinstance(m.get("content"), str))
    contents = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
        text = m.get("content")
        if not isinstance(text, str):
            text = " ".join(part.get("text", "") for part in (text or []) if isinstance(part, dict))
        contents.append(types.Content(role="model" if role == "assistant" else "user", parts=[types.Part.from_text(text=text)]))
    if image is not None and contents:
        b64, mime = image
        contents[-1].parts.insert(0, types.Part.from_bytes(data=base64.b64decode(b64), mime_type=mime))
    cfg = types.GenerateContentConfig(
        system_instruction=system or None,
        max_output_tokens=max_tokens,
        temperature=temperature,
        response_mime_type="application/json" if json_mode else None,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    gclient = genai.Client(api_key=key)  # keep a reference until the reply is read
    import time as _time
    for attempt in range(3):
        try:
            resp = gclient.models.generate_content(model=GEMINI_TEXT_MODEL, contents=contents, config=cfg)
            break
        except Exception as exc:
            busy = any(code in str(exc) for code in ("503", "UNAVAILABLE", "overloaded", "429"))
            if not busy or attempt == 2:
                raise
            logger.warning(f"Gemini busy ({str(exc)[:60]}), retrying in {1.5 * (attempt + 1):.0f}s")
            _time.sleep(1.5 * (attempt + 1))
    out = (resp.text or "").strip()
    if not out:
        raise RuntimeError("Gemini returned an empty reply")
    return out


def _groq_generate(messages: list[dict], max_tokens: int, temperature: float) -> str:
    from groq_client import groq_client
    if not groq_client.is_available():
        raise RuntimeError("Groq key missing")
    system = "\n".join(m["content"] for m in messages if m.get("role") == "system")
    convo = "\n\n".join(f"{m['role']}: {m['content']}" for m in messages if m.get("role") != "system")
    return groq_client.chat(convo, system=system, max_tokens=max_tokens)

class UnifiedAIClient:
    def __init__(self):
        self._provider = "OpenRouter"
        self._local_url = "http://localhost:11434/v1"
        self._local_model = "llama3.2"
        self.reload_settings()

    def reload_settings(self):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._provider = data.get("default_ai_provider", "OpenRouter")
            self._local_url = data.get("local_ai_url", "http://localhost:11434/v1").rstrip("/")
            self._local_model = data.get("local_ai_model", "llama3.2")
        except Exception as e:
            logger.error(f"[LLM Client] Failed to load settings: {e}")

    def _cloud_order(self) -> list[str]:
        """Preferred provider first, then whichever others have a key configured."""
        keys = _api_keys()
        have = {
            "Gemini": bool((keys.get("gemini_api_key") or "").strip()),
            "OpenRouter": bool((keys.get("openrouter_api_key") or "").strip()),
            "Groq": bool((keys.get("groq_api_key") or "").strip()),
        }
        pref = self._provider if self._provider in have else "Gemini"
        order = [pref] + [p for p in ("Gemini", "OpenRouter", "Groq") if p != pref]
        return [p for p in order if have[p]]

    def _cloud_chat(self, messages: list[dict], max_tokens: int, temperature: float,
                    model: Optional[str] = None, json_mode: bool = False) -> str:
        errors = []
        for provider in self._cloud_order():
            try:
                if provider == "Gemini":
                    return _gemini_generate(messages, max_tokens, temperature, json_mode=json_mode)
                if provider == "OpenRouter":
                    return openrouter_client.multi_turn(messages, model, max_tokens, temperature)
                if provider == "Groq":
                    return _groq_generate(messages, max_tokens, temperature)
            except Exception as exc:
                errors.append(f"{provider}: {exc}")
                logger.warning(f"[LLM Client] {provider} failed, trying next: {exc}")
        raise RuntimeError("No AI provider available. " + (" | ".join(errors) or "Add a Gemini key in config/api_keys.json."))

    def _local_chat_completion(self, messages: list[dict], temperature: float = 0.7, response_format: Optional[dict] = None) -> Optional[str]:
        payload = {
            "model": self._local_model,
            "messages": messages,
            "temperature": temperature
        }
        if response_format:
            payload["response_format"] = response_format

        endpoint = f"{self._local_url}/chat/completions"
        try:
            resp = requests.post(
                endpoint,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=120
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return content.strip() if content else None
            else:
                logger.error(f"[LLM Client] Local AI Error {resp.status_code}: {resp.text}")
                return None
        except Exception as e:
            logger.error(f"[LLM Client] Local AI Request Failed: {e}")
            return None

    def chat(self, prompt: str, system: str = "You are a helpful assistant.", history: Optional[list[dict]] = None, model: Optional[str] = None, max_tokens: int = 4096, temperature: float = 0.7) -> str:
        self.reload_settings()
        if self._provider == "Local":
            messages = [{"role": "system", "content": system}]
            if history:
                messages.extend(history)
            messages.append({"role": "user", "content": prompt})
            result = self._local_chat_completion(messages, temperature)
            if result:
                return result
            else:
                raise RuntimeError("Local AI request failed. Please check if Ollama or LM Studio is running.")
        else:
            messages = [{"role": "system", "content": system}] + list(history or []) + [{"role": "user", "content": prompt}]
            return self._cloud_chat(messages, max_tokens, temperature, model)

    def chat_json(self, prompt: str, system: str = "Return ONLY valid JSON.", model: Optional[str] = None, max_tokens: int = 4096) -> dict:
        self.reload_settings()
        if self._provider == "Local":
            messages = [
                {"role": "system", "content": system + " Output valid JSON only, without any markdown formatting."},
                {"role": "user", "content": prompt}
            ]
            raw = self._local_chat_completion(messages, temperature=0.2, response_format={"type": "json_object"})
            if not raw:
                raise RuntimeError("Local AI request failed.")
            
            clean = raw.strip()
            if clean.startswith("```"):
                parts = clean.split("```")
                clean = parts[1] if len(parts) > 1 else clean
                if clean.startswith("json"):
                    clean = clean[4:]
            clean = clean.strip().rstrip("`").strip()
            
            try:
                return json.loads(clean)
            except json.JSONDecodeError as e:
                raise ValueError(f"Local model returned unparseable JSON: {e}\nRaw output: {raw[:200]}")
        else:
            raw = self._cloud_chat(
                [{"role": "system", "content": system + " Output valid JSON only."}, {"role": "user", "content": prompt}],
                max_tokens, 0.2, model, json_mode=True,
            )
            clean = raw.strip()
            if clean.startswith("```"):
                clean = clean.strip("`")
                if clean.startswith("json"):
                    clean = clean[4:]
            return json.loads(clean.strip())

    def vision(self, prompt: str, image_b64: str, mime: str = "image/png", system: str = "Analyze the image.", model: Optional[str] = None, max_tokens: int = 1024) -> str:
        self.reload_settings()
        if self._provider == "Local":
            messages = [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                        {"type": "text", "text": prompt}
                    ]
                }
            ]
            result = self._local_chat_completion(messages, temperature=0.2)
            if result:
                return result
            raise RuntimeError("Local AI vision request failed.")
        else:
            order = self._cloud_order()
            if order and order[0] == "Gemini" or ("Gemini" in order and "OpenRouter" not in order):
                try:
                    return _gemini_generate(
                        [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                        max_tokens, 0.2, image=(image_b64, mime),
                    )
                except Exception as exc:
                    if "OpenRouter" not in order:
                        raise
                    logger.warning(f"[LLM Client] Gemini vision failed, trying OpenRouter: {exc}")
            return openrouter_client.vision(prompt, image_b64, mime, system, model, max_tokens)

    def vision_from_file(self, prompt: str, image_path: str, system: str = "Analyze the image.", model: Optional[str] = None, max_tokens: int = 1024) -> str:
        self.reload_settings()
        if True:
            import base64
            path = Path(image_path)
            mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
            mime = mime_map.get(path.suffix.lower(), "image/png")
            with open(path, "rb") as f:
                image_b64 = base64.b64encode(f.read()).decode("utf-8")
            return self.vision(prompt, image_b64, mime, system, model, max_tokens)
        else:
            return openrouter_client.vision_from_file(prompt, image_path, system, model, max_tokens)

    def multi_turn(self, messages: list[dict], model: Optional[str] = None, max_tokens: int = 4096, temperature: float = 0.7) -> str:
        self.reload_settings()
        if self._provider == "Local":
            result = self._local_chat_completion(messages, temperature)
            if result:
                return result
            raise RuntimeError("Local AI request failed.")
        else:
            return self._cloud_chat(messages, max_tokens, temperature, model)

client = UnifiedAIClient()
