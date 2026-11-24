from core.secrets import KEYS_FILE  # keys/tokens are encrypted at rest (DPAPI)
from core.paths import DATA_DIR  # owner data lives apart from the program
import json, os
from pathlib import Path

_CONFIG_PATH = KEYS_FILE

def get_config() -> dict:
    with _CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)

def get_os() -> str:
    """Returns: 'windows' | 'mac' | 'linux'"""
    return get_config().get("os_system", "windows").lower()

def is_windows() -> bool: return get_os() == "windows"
def is_mac()     -> bool: return get_os() == "mac"
def is_linux()   -> bool: return get_os() == "linux"