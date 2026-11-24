#web_search.py
from core.secrets import KEYS_FILE  # keys/tokens are encrypted at rest (DPAPI)
from core.paths import DATA_DIR  # owner data lives apart from the program
import json
import sys
import warnings
from pathlib import Path

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = KEYS_FILE


def _get_api_key() -> str:
    with API_CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _gemini_search(query: str) -> str:
    from google import genai

    client   = genai.Client(api_key=_get_api_key())
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=query,
        config={"tools": [{"google_search": {}}]},
    )

    text = ""
    for part in response.candidates[0].content.parts:
        if hasattr(part, "text") and part.text:
            text += part.text

    text = text.strip()
    if not text:
        raise ValueError("Gemini returned an empty response.")
    return text


def _bing_search(query: str, max_results: int = 6) -> list[dict]:
    """Plain Bing results page → real result URLs (Bing's /ck/a links carry the URL base64-encoded)."""
    import base64
    import requests
    from bs4 import BeautifulSoup
    from urllib.parse import parse_qs, urlparse

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/130.0 Safari/537.36", "Accept-Language": "en-IN,en;q=0.9"}
    r = requests.get("https://www.bing.com/search", params={"q": query, "setlang": "en"}, headers=headers, timeout=15)
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for li in soup.select("li.b_algo"):
        h = li.select_one("h2 a")
        if not h:
            continue
        href = h.get("href", "")
        if "bing.com/ck/a" in href:
            u = (parse_qs(urlparse(href).query).get("u") or [""])[0]
            if u.startswith("a1"):
                try:
                    enc = u[2:]
                    href = base64.urlsafe_b64decode(enc + "=" * (-len(enc) % 4)).decode("utf-8", "replace")
                except Exception:
                    pass
        if not href.startswith("http"):
            continue
        snip = li.select_one(".b_caption p") or li.select_one("p")
        out.append({"title": h.get_text(" ", strip=True), "snippet": snip.get_text(" ", strip=True) if snip else "", "url": href})
        if len(out) >= max_results:
            break
    return out


def _ddg_search(query: str, max_results: int = 6) -> list[dict]:
    """DuckDuckGo first; the old duckduckgo_search package now often returns nothing, so fall back to Bing."""
    try:
        results = _ddg_search_raw(query, max_results)
    except Exception:
        results = []
    if not results:
        try:
            results = _bing_search(query, max_results)
        except Exception:
            results = []
    return results


def _ddg_search_raw(query: str, max_results: int = 6) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                category=RuntimeWarning,
                message=r"This package .* has been renamed to .*",
            )
            from duckduckgo_search import DDGS

    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append({
                "title":   r.get("title",  ""),
                "snippet": r.get("body",   ""),
                "url":     r.get("href",   ""),
            })
    return results


def _format_ddg(query: str, results: list[dict]) -> str:
    if not results:
        return f"No results found for: {query}"

    lines = [f"Search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        if r.get("title"):   lines.append(f"{i}. {r['title']}")
        if r.get("snippet"): lines.append(f"   {r['snippet']}")
        if r.get("url"):     lines.append(f"   {r['url']}")
        lines.append("")
    return "\n".join(lines).strip()

def _compare(items: list[str], aspect: str) -> str:
    query = (
        f"Compare {', '.join(items)} in terms of {aspect}. "
        "Give specific facts and data."
    )
    try:
        return _gemini_search(query)
    except Exception as e:
        print(f"[WebSearch] ⚠️ Gemini compare failed: {e} — falling back to DDG")

    # DDG fallback: fetch results per item and merge
    all_results: dict[str, list] = {}
    for item in items:
        try:
            all_results[item] = _ddg_search(f"{item} {aspect}", max_results=3)
        except Exception:
            all_results[item] = []

    lines = [f"Comparison — {aspect.upper()}", "─" * 40]
    for item in items:
        lines.append(f"\n▸ {item}")
        for r in all_results.get(item, [])[:2]:
            if r.get("snippet"):
                lines.append(f"  • {r['snippet']}")
    return "\n".join(lines)

def web_search(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    query  = params.get("query", "").strip()
    mode   = params.get("mode",  "search").lower().strip()
    items  = params.get("items", [])
    aspect = params.get("aspect", "general").strip() or "general"

    if not query and not items:
        return "Please provide a search query, sir."

    if items and mode != "compare":
        mode = "compare"

    if player:
        player.write_log(f"[Search] {query or ', '.join(items)}")

    print(f"[WebSearch] Query: {query!r}  Mode: {mode}")
    if mode == "compare":
        try:
            result = _compare(items or ([query] if query else []), aspect)
            print("[WebSearch] Gemini compare OK.")
            return result
        except Exception as e:
            print(f"[WebSearch] Gemini compare failed ({e}) - trying DDG...")
            all_results: dict[str, list] = {}
            for item in items or ([query] if query else []):
                try:
                    all_results[item] = _ddg_search(f"{item} {aspect}", max_results=3)
                except Exception:
                    all_results[item] = []

            lines = [f"Comparison — {aspect.upper()}", "─" * 40]
            for item in items or ([query] if query else []):
                lines.append(f"\n▸ {item}")
                for r in all_results.get(item, [])[:2]:
                    if r.get("snippet"):
                        lines.append(f"  • {r['snippet']}")
            return "\n".join(lines).strip()

    try:
        results = _ddg_search(query)
        if results:
            result = _format_ddg(query, results)
            print(f"[WebSearch] DDG OK: {len(results)} result(s).")
            return result
        print("[WebSearch] DDG returned no results, trying Gemini...")
    except Exception as e:
        print(f"[WebSearch] DDG search failed ({e}) - trying Gemini...")
        try:
            result = _gemini_search(query)
            print("[WebSearch] Gemini search OK.")
            return result
        except Exception as gemini_error:
            print(f"[WebSearch] Gemini search failed ({gemini_error})")
            return f"Search failed, sir: {gemini_error}"

    try:
        result = _gemini_search(query)
        print("[WebSearch] Gemini search OK.")
        return result
    except Exception:
        return _format_ddg(query, results)
