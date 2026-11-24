"""website_analyzer: study a live website and produce a build blueprint.

Opens a page in headless Chromium (Playwright), much like the reverse-engineering
"web page inspection" tools, and extracts what you'd need to build your **own**
version:

* tech stack (frameworks, libraries, analytics, CSS system, hosting hints)
* page structure (landmarks, section outline, headings)
* design system (colour palette, fonts, type scale, radii, shadows, spacing)
* components (nav, buttons, forms, cards, media)
* behaviour (animations and transitions, scroll effects, API endpoints the page calls)
* responsive behaviour (desktop and mobile screenshots)

Output goes to ``<project>/analysis/<host>-<timestamp>/``:
``blueprint.md`` (human readable), ``blueprint.json`` (machine readable),
``desktop.png`` and ``mobile.png``.

Ethics built in: the blueprint captures *patterns*, not other people's
copyrighted text, logos or images. It loads one public page the way a normal
browser would, and does not crawl, log in or bypass protections.
"""

from __future__ import annotations

from core.paths import CACHE_DIR  # owner data lives apart from the program
import json
import re
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
OUT_ROOT = CACHE_DIR / "analysis"

# (label, detector) pairs evaluated in the page. Each detector is a JS expression returning truthy.
_TECH_JS = r"""
() => {
  const w = window, d = document;
  const srcs = [...d.scripts].map(s => s.src || '').join(' ');
  const html = d.documentElement.outerHTML.slice(0, 400000);
  const has = (re) => re.test(srcs) || re.test(html);
  const t = {};
  t['React'] = !!(w.React || d.querySelector('[data-reactroot]') || has(/react(-dom)?[.@-]/i) || [...d.querySelectorAll('*')].slice(0,400).some(e => Object.keys(e).some(k => k.startsWith('__react'))));
  t['Next.js'] = !!(w.__NEXT_DATA__ || d.getElementById('__next') || has(/\/_next\//));
  t['Vue'] = !!(w.Vue || w.__VUE__ || d.querySelector('[data-v-app]') || has(/vue(\.runtime)?[.@-]/i));
  t['Nuxt'] = !!(w.__NUXT__ || has(/\/_nuxt\//));
  t['Angular'] = !!(d.querySelector('[ng-version]') || w.ng);
  t['Svelte/SvelteKit'] = has(/svelte|__sveltekit/i);
  t['Astro'] = has(/astro-island|\/_astro\//i);
  t['Remix'] = !!w.__remixContext;
  t['Gatsby'] = !!(d.getElementById('___gatsby'));
  t['jQuery'] = !!w.jQuery;
  t['Tailwind CSS'] = /\b(flex|grid) .*\b(px|py|mt|mb)-\d|\btext-(sm|lg|xl)\b/.test(html) && /\b(bg|text)-[a-z]+-\d{2,3}\b/.test(html);
  t['Bootstrap'] = has(/bootstrap/i) || !!d.querySelector('.container .row .col, [class*="col-md-"]');
  t['styled-components/emotion'] = !!d.querySelector('style[data-styled], style[data-emotion]');
  t['GSAP'] = !!(w.gsap || w.TweenMax);
  t['Framer Motion'] = has(/framer-motion/i);
  t['Three.js'] = !!w.THREE || has(/three(\.module)?(\.min)?\.js/i);
  t['Lottie'] = !!w.lottie || has(/lottie/i);
  t['Swiper'] = !!w.Swiper || has(/swiper/i);
  t['Lenis/Locomotive (smooth scroll)'] = has(/lenis|locomotive-scroll/i);
  t['Google Analytics/GTM'] = !!(w.gtag || w.ga || w.dataLayer);
  t['Segment'] = !!w.analytics && has(/segment/i);
  t['Hotjar'] = !!w.hj;
  t['Stripe'] = !!w.Stripe || has(/js\.stripe\.com/);
  t['Intercom'] = !!w.Intercom;
  t['Shopify'] = !!w.Shopify;
  t['WordPress'] = has(/wp-content|wp-includes/);
  t['Webflow'] = !!d.documentElement.getAttribute('data-wf-site');
  t['Framer (site builder)'] = has(/framerusercontent|framer\.com\/m\//i);
  t['Wix'] = has(/wixstatic|wix\.com/i);
  t['Cloudflare'] = has(/cdn-cgi|cloudflare/i);
  t['Vercel'] = has(/vercel/i);
  return Object.entries(t).filter(([k, v]) => v).map(([k]) => k);
}
"""

_DESIGN_JS = r"""
() => {
  const els = [...document.querySelectorAll('body *')].filter(e => {
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }).slice(0, 2500);
  const colors = {}, bgs = {}, fonts = {}, sizes = {}, weights = {}, radii = {}, shadows = {}, gaps = {}, transitions = {}, animations = {};
  const bump = (o, k, n = 1) => { if (k && k !== 'none' && k !== 'normal' && k !== '0px' && k !== 'rgba(0, 0, 0, 0)') o[k] = (o[k] || 0) + n; };
  for (const e of els) {
    const s = getComputedStyle(e);
    const area = Math.min(e.getBoundingClientRect().width * e.getBoundingClientRect().height, 200000);
    const hasText = [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length > 1);
    if (hasText) {
      bump(colors, s.color, 1);
      bump(fonts, s.fontFamily.split(',')[0].replace(/["']/g, '').trim(), 1);
      bump(sizes, s.fontSize, 1);
      bump(weights, s.fontWeight, 1);
    }
    bump(bgs, s.backgroundColor, Math.ceil(area / 5000));
    bump(radii, s.borderRadius);
    bump(shadows, s.boxShadow);
    bump(gaps, s.gap);
    bump(gaps, s.paddingTop);
    if (s.transitionDuration && s.transitionDuration !== '0s') bump(transitions, s.transitionProperty + ' ' + s.transitionDuration + ' ' + s.transitionTimingFunction);
    if (s.animationName && s.animationName !== 'none') bump(animations, s.animationName + ' ' + s.animationDuration);
  }
  const top = (o, n) => Object.entries(o).sort((a, b) => b[1] - a[1]).slice(0, n).map(([k, v]) => ({value: k, count: v}));
  const rootVars = {};
  try {
    for (const sheet of document.styleSheets) {
      let rules; try { rules = sheet.cssRules; } catch (e) { continue; }
      for (const r of rules) {
        if (r.selectorText === ':root' || r.selectorText === 'html') {
          for (const p of r.style) if (p.startsWith('--') && Object.keys(rootVars).length < 80) rootVars[p] = r.style.getPropertyValue(p).trim();
        }
      }
    }
  } catch (e) {}
  const kf = [];
  try {
    for (const sheet of document.styleSheets) {
      let rules; try { rules = sheet.cssRules; } catch (e) { continue; }
      for (const r of rules) if (r.type === 7 && kf.length < 25) kf.push(r.name);
    }
  } catch (e) {}
  return {
    text_colors: top(colors, 10), backgrounds: top(bgs, 10), fonts: top(fonts, 6),
    font_sizes: top(sizes, 12), font_weights: top(weights, 6), radii: top(radii, 8),
    shadows: top(shadows, 6), spacing: top(gaps, 12), transitions: top(transitions, 8),
    animations: top(animations, 8), keyframes: kf, css_variables: rootVars,
  };
}
"""

_STRUCTURE_JS = r"""
() => {
  const d = document;
  const txt = (e, n = 80) => (e.innerText || e.textContent || '').trim().replace(/\s+/g, ' ').slice(0, n);
  const landmarks = ['header', 'nav', 'main', 'aside', 'footer'].map(t => ({tag: t, count: d.querySelectorAll(t).length}));
  const headings = [...d.querySelectorAll('h1,h2,h3')].slice(0, 60).map(h => ({level: h.tagName, text: txt(h)}));
  const sections = [...d.querySelectorAll('main > *, body > section, main section, [class*="section"]')]
    .filter(e => e.getBoundingClientRect().height > 120).slice(0, 30).map((e, i) => {
      const r = e.getBoundingClientRect();
      const h = e.querySelector('h1,h2,h3');
      const cols = getComputedStyle(e).display;
      return {
        index: i, tag: e.tagName.toLowerCase(), heading: h ? txt(h, 60) : '',
        height_px: Math.round(r.height), display: cols,
        has_media: !!e.querySelector('img,video,canvas,svg'),
        buttons: e.querySelectorAll('button,a[class*="btn"],a[class*="button"]').length,
        cards: e.querySelectorAll('[class*="card"],article,li').length,
      };
    });
  const nav = [...d.querySelectorAll('header a, nav a')].slice(0, 25).map(a => txt(a, 30)).filter(Boolean);
  const comp = {
    buttons: d.querySelectorAll('button, [role="button"], a[class*="btn"], a[class*="button"]').length,
    forms: d.querySelectorAll('form').length,
    inputs: d.querySelectorAll('input, textarea, select').length,
    images: d.querySelectorAll('img').length,
    videos: d.querySelectorAll('video').length,
    canvases: d.querySelectorAll('canvas').length,
    svgs: d.querySelectorAll('svg').length,
    iframes: d.querySelectorAll('iframe').length,
    cards: d.querySelectorAll('[class*="card"], article').length,
    modals_dialogs: d.querySelectorAll('dialog, [role="dialog"], [class*="modal"]').length,
    carousels: d.querySelectorAll('[class*="carousel"], [class*="slider"], [class*="swiper"]').length,
    sticky_elements: [...d.querySelectorAll('header, nav, div')].slice(0, 800).filter(e => ['sticky', 'fixed'].includes(getComputedStyle(e).position)).length,
  };
  const meta = {
    title: d.title,
    description: (d.querySelector('meta[name="description"]') || {}).content || '',
    lang: d.documentElement.lang || '',
    viewport: (d.querySelector('meta[name="viewport"]') || {}).content || '',
    theme_color: (d.querySelector('meta[name="theme-color"]') || {}).content || '',
    og_type: (d.querySelector('meta[property="og:type"]') || {}).content || '',
  };
  return {meta, landmarks, headings, sections, nav, components: comp, page_height: d.documentElement.scrollHeight};
}
"""


def _launch_browser(pw):
    """Use Playwright's bundled Chromium if present, else the installed Edge or Chrome."""
    errors = []
    for kwargs in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
        try:
            return pw.chromium.launch(headless=True, **kwargs)
        except Exception as exc:
            errors.append(str(exc).splitlines()[0][:120])
    raise RuntimeError("No usable browser found. Run 'playwright install chromium'. " + " | ".join(errors))


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9.-]+", "-", text).strip("-")[:60] or "site"


def analyze(url: str, out_root: Path = OUT_ROOT, timeout_ms: int = 45000) -> dict:
    from playwright.sync_api import sync_playwright

    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Please give a normal http(s) website address.")

    out_dir = out_root / f"{_slug(parsed.netloc)}-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)

    api_calls: Counter = Counter()
    resource_types: Counter = Counter()
    third_party: Counter = Counter()
    response_headers: dict = {}

    with sync_playwright() as pw:
        browser = _launch_browser(pw)
        try:
            ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
            page = ctx.new_page()

            def on_request(req):
                resource_types[req.resource_type] += 1
                host = urlparse(req.url).netloc
                if host and host != parsed.netloc and not host.endswith("." + parsed.netloc.split(":")[0].removeprefix("www.")):
                    third_party[host] += 1
                if req.resource_type in {"xhr", "fetch"}:
                    u = urlparse(req.url)
                    api_calls[f"{req.method} {u.netloc}{u.path}"] += 1

            page.on("request", on_request)
            resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass
            if resp is not None:
                keep = {"server", "x-powered-by", "via", "x-vercel-id", "cf-ray", "x-nextjs-cache", "content-security-policy", "x-frame-options"}
                response_headers = {k: v[:200] for k, v in resp.headers.items() if k.lower() in keep}

            # Scroll through once so lazy content and scroll animations initialise.
            page.evaluate("""async () => { for (let y = 0; y < document.body.scrollHeight && y < 20000; y += 700) { window.scrollTo(0, y); await new Promise(r => setTimeout(r, 120)); } window.scrollTo(0, 0); }""")
            page.wait_for_timeout(600)

            tech = page.evaluate(_TECH_JS)
            design = page.evaluate(_DESIGN_JS)
            structure = page.evaluate(_STRUCTURE_JS)
            page.screenshot(path=str(out_dir / "desktop.png"), full_page=True, timeout=30000)

            mobile = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
            mpage = mobile.new_page()
            try:
                mpage.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                mpage.wait_for_timeout(2500)
                mpage.screenshot(path=str(out_dir / "mobile.png"), full_page=False)
                mobile_nav = mpage.evaluate("() => !!document.querySelector('[class*=\"hamburger\"], [class*=\"menu-toggle\"], button[aria-label*=\"menu\" i], [aria-controls*=\"menu\" i]')")
            except Exception:
                mobile_nav = None
            mobile.close()
        finally:
            browser.close()

    if "x-powered-by" in {k.lower() for k in response_headers}:
        tech.append(f"Server says: {response_headers.get('x-powered-by')}")

    blueprint = {
        "url": url,
        "analysed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tech_stack": tech,
        "server_headers": response_headers,
        "structure": structure,
        "design_system": design,
        "behaviour": {
            "api_endpoints": [{"call": k, "count": v} for k, v in api_calls.most_common(25)],
            "resource_types": dict(resource_types),
            "third_party_services": [{"host": k, "requests": v} for k, v in third_party.most_common(20)],
            "mobile_hamburger_menu": mobile_nav,
        },
        "files": {"desktop_screenshot": str(out_dir / "desktop.png"), "mobile_screenshot": str(out_dir / "mobile.png")},
    }
    (out_dir / "blueprint.json").write_text(json.dumps(blueprint, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "blueprint.md").write_text(_to_markdown(blueprint), encoding="utf-8")
    blueprint["folder"] = str(out_dir)
    return blueprint


def _vals(items, n=6) -> str:
    return ", ".join(f"{i['value']}" for i in (items or [])[:n]) or "-"


def _to_markdown(b: dict) -> str:
    s, d, beh = b["structure"], b["design_system"], b["behaviour"]
    lines = [
        f"# Build blueprint: {s['meta'].get('title') or b['url']}",
        "",
        f"Source studied: {b['url']}, on {b['analysed_at']}",
        "",
        "> Use this to build your **own** site with the same structure and feel.",
        "> Write your own copy and use your own logo and imagery. Don't reuse the original brand's.",
        "",
        "## Tech stack detected",
        "- " + ("\n- ".join(b["tech_stack"]) if b["tech_stack"] else "Nothing conclusive (likely server-rendered or custom)."),
        "",
        "## Page structure",
        "Landmarks: " + ", ".join(f"{l['tag']}×{l['count']}" for l in s["landmarks"] if l["count"]),
        f"Page height: {s['page_height']}px · Nav items: {len(s['nav'])}",
        "",
        "### Section outline (top to bottom)",
    ]
    for sec in s["sections"]:
        tags = []
        if sec["has_media"]:
            tags.append("media")
        if sec["buttons"]:
            tags.append(f"{sec['buttons']} CTA")
        if sec["cards"] > 2:
            tags.append(f"{sec['cards']} items/cards")
        lines.append(f"{sec['index'] + 1}. **{sec['heading'] or sec['tag']}**: {sec['height_px']}px, {sec['display']}" + (f" ({', '.join(tags)})" if tags else ""))
    lines += [
        "",
        "## Design system",
        f"- **Fonts:** {_vals(d['fonts'])}",
        f"- **Type scale:** {_vals(d['font_sizes'], 10)}",
        f"- **Weights:** {_vals(d['font_weights'])}",
        f"- **Text colours:** {_vals(d['text_colors'])}",
        f"- **Backgrounds (by area):** {_vals(d['backgrounds'])}",
        f"- **Corner radii:** {_vals(d['radii'])}",
        f"- **Shadows:** {_vals(d['shadows'], 4)}",
        f"- **Spacing in use:** {_vals(d['spacing'], 10)}",
    ]
    if d.get("css_variables"):
        lines.append("- **CSS design tokens (:root):**")
        for k, v in list(d["css_variables"].items())[:30]:
            lines.append(f"  - `{k}: {v}`")
    lines += [
        "",
        "## Motion and interaction",
        f"- Transitions: {_vals(d['transitions'], 5)}",
        f"- Animations: {_vals(d['animations'], 5)}",
        f"- Keyframes defined: {', '.join(d.get('keyframes') or []) or '-'}",
        f"- Mobile hamburger menu: {beh.get('mobile_hamburger_menu')}",
        "",
        "## Components",
    ]
    lines += [f"- {k.replace('_', ' ')}: {v}" for k, v in s["components"].items() if v]
    lines += ["", "## Data and integrations"]
    lines += [f"- `{a['call']}` ×{a['count']}" for a in beh["api_endpoints"][:15]] or ["- No XHR/fetch calls observed on load."]
    if beh["third_party_services"]:
        lines.append("- Third-party hosts: " + ", ".join(t["host"] for t in beh["third_party_services"][:12]))
    lines += [
        "",
        "## How to build your version",
        "1. Pick a stack: match the detected one, or use Next.js + Tailwind for the same feel with less effort.",
        "2. Turn the design tokens above into your own theme, then shift the palette and fonts toward your brand.",
        "3. Build the sections in the outline order as reusable components.",
        "4. Add the motion layer last (transitions and keyframes above), then check against mobile.png.",
        "5. Replace every piece of text and imagery with your own.",
    ]
    return "\n".join(lines) + "\n"


def website_analyzer(parameters: dict, player=None, **_) -> str:
    url = str(parameters.get("url") or "").strip()
    if not url:
        return "Tell me which website to analyse."
    if player:
        try:
            player.write_log(f"SYS: Analysing {url} ...")
        except Exception:
            pass
    try:
        b = analyze(url)
    except Exception as exc:
        return f"Couldn't analyse {url}: {exc}"
    s, d = b["structure"], b["design_system"]
    summary = (
        f"Analysed {b['url']}. Stack: {', '.join(b['tech_stack']) or 'unclear'}. "
        f"{len(s['sections'])} main sections, {s['components']['buttons']} buttons, {s['components']['forms']} forms. "
        f"Fonts: {_vals(d['fonts'], 3)}. Main colours: {_vals(d['backgrounds'], 3)}. "
        f"API calls seen: {len(b['behaviour']['api_endpoints'])}. "
        f"Full blueprint and screenshots saved in {b['folder']} (blueprint.md)."
    )
    if parameters.get("open_folder", True):
        try:
            import os
            os.startfile(b["folder"])  # type: ignore[attr-defined]
        except Exception:
            pass
    return summary
