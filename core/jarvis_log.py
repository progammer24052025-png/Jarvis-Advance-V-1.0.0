"""Readable logs for Jarvis.

Console: only what matters, with a time stamp on every line
    21:04:12  [Jarvis] 🔧 web_search  {'query': '...'}
    21:04:13  [Guardian] ✋ held send_message
    21:04:15  WARN  google_genai: 503 Service Unavailable, retrying

Library chatter (HTTP request lines, Google "AFC" notices, deprecation
warnings) is kept out of the console and written to the log file only.

File: logs/jarvis.log (rotates at 5 MB, keeps 3 old files). Full detail with
dates, for when something goes wrong and you need the whole story.
"""

from __future__ import annotations

import io
import logging
import logging.handlers
import sys
import threading
import warnings
from datetime import datetime
from pathlib import Path

# Libraries that log every request at INFO. Their detail goes to the file only.
_CHATTY = ("httpx", "httpcore", "google_genai", "google_genai.models", "google_genai.live",
           "websockets", "urllib3", "asyncio", "PIL", "comtypes", "openrouter_client",
           "llm_client", "instagrapi", "public_request", "private_request", "playwright",
           "primp", "duckduckgo_search", "ddgs", "google_genai.types")

_installed = False


class _TimestampTee(io.TextIOBase):
    """Wraps stdout/stderr: prefixes each line with HH:MM:SS and mirrors it to the log file."""

    def __init__(self, stream, file_logger: logging.Logger, level: int):
        self._stream = stream
        self._log = file_logger
        self._level = level
        self._buf = ""
        self._lock = threading.Lock()

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        if not text:
            return 0
        with self._lock:
            self._buf += text
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                self._emit(line)
        return len(text)

    def _emit(self, line: str) -> None:
        if not line.strip():
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        # Developer debug prints go to the file only.
        quiet = line.lstrip().startswith(("DEBUG", "[DEBUG]"))
        try:
            if self._stream is not None and not quiet:
                out = f"{stamp}  {line}\n"
                try:
                    self._stream.write(out)
                except UnicodeEncodeError:
                    enc = getattr(self._stream, "encoding", None) or "ascii"
                    self._stream.write(out.encode(enc, "replace").decode(enc))
                self._stream.flush()
        except Exception:
            pass
        try:
            self._log.log(self._level, line)
        except Exception:
            pass

    def flush(self) -> None:
        try:
            if self._stream is not None:
                self._stream.flush()
        except Exception:
            pass

    # some libraries probe these
    @property
    def encoding(self):
        return getattr(self._stream, "encoding", "utf-8")

    def isatty(self) -> bool:
        return False

    def fileno(self):
        if self._stream is None:
            raise OSError("no underlying stream")
        return self._stream.fileno()


class _ConsoleFilter(logging.Filter):
    """Console shows Jarvis's own INFO, and WARNING+ from anyone (except deprecation noise)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "py.warnings":
            return False
        chatty = any(record.name == c or record.name.startswith(c + ".") for c in _CHATTY)
        if record.name == "google_genai.types":
            return False  # "non-data parts in the response" notices: harmless
        if record.levelno >= logging.WARNING:
            return True
        return not chatty


def setup_logging(base_dir: Path) -> Path:
    """Call once, as early as possible in main.py. Returns the log file path."""
    global _installed
    log_dir = Path(base_dir) / "logs"
    log_path = log_dir / "jarvis.log"
    if _installed:
        return log_path
    _installed = True
    log_dir.mkdir(parents=True, exist_ok=True)

    file_handler = logging.handlers.RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-5s  %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
    file_handler.setLevel(logging.INFO)

    # Plain prints are captured by the tee (which stamps them), so this logger only feeds the file.
    out_logger = logging.getLogger("jarvis.console")
    out_logger.propagate = False
    out_logger.addHandler(file_handler)
    out_logger.setLevel(logging.INFO)

    original_out = sys.__stdout__ if sys.stdout is None else sys.stdout
    original_err = sys.__stderr__ if sys.stderr is None else sys.stderr
    # Emoji-safe console on Windows (cp1252 consoles would otherwise drop lines).
    for _s in (original_out, original_err):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    console = logging.StreamHandler(original_err)
    console.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-5s %(name)s: %(message)s", "%H:%M:%S"))
    console.addFilter(_ConsoleFilter())
    console.setLevel(logging.INFO)

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(file_handler)
    root.addHandler(console)
    root.setLevel(logging.INFO)

    # Library deprecation warnings → file only.
    logging.captureWarnings(True)
    warnings.simplefilter("default", DeprecationWarning)

    sys.stdout = _TimestampTee(original_out, out_logger, logging.INFO)
    sys.stderr = _TimestampTee(original_err, out_logger, logging.WARNING)

    logging.getLogger("jarvis").info("──────── Jarvis starting ────────")
    return log_path
