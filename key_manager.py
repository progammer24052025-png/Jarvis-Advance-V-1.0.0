"""
key_manager.py — Per-provider API key rotation for Jarvis

Supports multiple keys per provider with round-robin rotation.
If a key hits a rate limit, it is temporarily cooled down and the
next key in the pool is used automatically.

Schema in config/api_keys.json:
{
    "gemini_api_key":     "key1",              # single key (legacy)
    "gemini_api_keys":    ["key1", "key2"],    # multi-key (new)
    "groq_api_key":       "",
    "groq_api_keys":      ["key1", "key2", "key3"],
    "openrouter_api_key": "",
    "openrouter_api_keys":["key1", "key2"],
    "anthropic_api_key":  "",
    "anthropic_api_keys": ["key1"],
    "os_system":          "Windows"
}

Single keys are automatically treated as a 1-item pool.
"""

from __future__ import annotations

from core.secrets import KEYS_FILE  # keys/tokens are encrypted at rest (DPAPI)
from core.paths import DATA_DIR  # owner data lives apart from the program
import json
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# ── Path helpers ──────────────────────────────────────────────────────────────

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = _base_dir()
API_CONFIG_PATH = KEYS_FILE

# How long (seconds) a key is cooled down after a rate-limit error
KEY_COOLDOWN_SECONDS = 60


# ── Internal state (per provider) ─────────────────────────────────────────────

class _ProviderState:
    def __init__(self, provider: str) -> None:
        self.provider    = provider
        self._lock       = threading.Lock()
        self._keys: list[str]         = []
        self._index: int              = 0
        self._cooldowns: dict[str, float] = {}  # key → expiry timestamp

    # ── Key loading ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Read keys from api_keys.json — supports both single and array forms."""
        try:
            with API_CONFIG_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            self._keys = []
            return

        plural_key  = f"{self.provider}_api_keys"    # array form
        singular_key = f"{self.provider}_api_key"    # legacy single form

        raw_list = data.get(plural_key, [])
        if isinstance(raw_list, str):
            raw_list = [raw_list]

        # Also pull in the singular key if it isn't already in the list
        singular = str(data.get(singular_key, "") or "").strip()
        if singular and singular not in raw_list:
            raw_list = [singular] + list(raw_list)

        self._keys = [k.strip() for k in raw_list if isinstance(k, str) and k.strip()]

    # ── Public API ────────────────────────────────────────────────────────────

    def get_key(self) -> Optional[str]:
        """
        Return the next available, non-cooled-down key using round-robin.
        Returns None if no keys are configured or all are rate-limited.
        """
        with self._lock:
            self._load()
            if not self._keys:
                return None

            now = time.time()
            # Try each key in rotation, starting from _index
            for _ in range(len(self._keys)):
                key = self._keys[self._index % len(self._keys)]
                self._index = (self._index + 1) % len(self._keys)
                expiry = self._cooldowns.get(key, 0)
                if now >= expiry:
                    return key

            # All keys are cooled down — return the least-recently-cooled one anyway
            best = min(self._cooldowns, key=lambda k: self._cooldowns[k], default=None)
            return best or self._keys[0]

    def mark_rate_limited(self, key: str, cooldown: float = KEY_COOLDOWN_SECONDS) -> None:
        """Mark a key as rate-limited for `cooldown` seconds."""
        with self._lock:
            self._cooldowns[key] = time.time() + cooldown
            print(f"[KeyManager] ⚠️  {self.provider} key ...{key[-6:]} rate-limited for {cooldown}s")

    def is_available(self) -> bool:
        """True if at least one key is configured for this provider."""
        with self._lock:
            self._load()
            return bool(self._keys)

    def all_keys(self) -> list[str]:
        """Return all configured keys (for display / debug)."""
        with self._lock:
            self._load()
            return list(self._keys)

    def pool_size(self) -> int:
        with self._lock:
            self._load()
            return len(self._keys)


# ── Singleton registry ────────────────────────────────────────────────────────

_registry: dict[str, _ProviderState] = {}
_registry_lock = threading.Lock()


def _get_provider(provider: str) -> _ProviderState:
    with _registry_lock:
        if provider not in _registry:
            _registry[provider] = _ProviderState(provider)
        return _registry[provider]


# ── Public helpers ────────────────────────────────────────────────────────────

def get_key(provider: str) -> Optional[str]:
    """
    Get the next available key for a provider using round-robin rotation.

    Args:
        provider: One of "gemini", "groq", "openrouter", "anthropic"

    Returns:
        API key string, or None if not configured.
    """
    return _get_provider(provider).get_key()


def mark_rate_limited(provider: str, key: str, cooldown: float = KEY_COOLDOWN_SECONDS) -> None:
    """Signal that a key hit a rate limit; it will be skipped for `cooldown` seconds."""
    _get_provider(provider).mark_rate_limited(key, cooldown)


def is_available(provider: str) -> bool:
    """Returns True if at least one key is configured for the provider."""
    return _get_provider(provider).is_available()


def pool_size(provider: str) -> int:
    """Number of keys in the pool for a provider."""
    return _get_provider(provider).pool_size()


def all_keys(provider: str) -> list[str]:
    """All keys for a provider (plain list)."""
    return _get_provider(provider).all_keys()


def status() -> dict:
    """Return a summary of all providers and their pool sizes."""
    providers = ["gemini", "groq", "openrouter", "anthropic"]
    return {
        p: {
            "pool_size":  pool_size(p),
            "available":  is_available(p),
        }
        for p in providers
    }


# ── Config helpers ────────────────────────────────────────────────────────────

def save_keys(provider: str, keys: list[str]) -> bool:
    """
    Persist a list of keys for a provider to api_keys.json.
    Also updates the legacy single-key field for backward compatibility.
    """
    try:
        try:
            with API_CONFIG_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

        clean_keys = [k.strip() for k in keys if isinstance(k, str) and k.strip()]
        data[f"{provider}_api_keys"] = clean_keys
        # Keep legacy field pointing at first key
        data[f"{provider}_api_key"] = clean_keys[0] if clean_keys else ""

        with API_CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

        # Invalidate cached state so next get_key() re-reads
        with _registry_lock:
            _registry.pop(provider, None)

        print(f"[KeyManager] ✅ Saved {len(clean_keys)} key(s) for {provider}")
        return True
    except Exception as e:
        print(f"[KeyManager] ❌ Failed to save keys for {provider}: {e}")
        return False
