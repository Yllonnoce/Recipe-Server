"""URL capture: render the page to PDF with headless Chromium and pull the
structured recipe out of the page's markup with recipe-scrapers."""
from __future__ import annotations

import io
import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from ...config import get_settings

log = logging.getLogger(__name__)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0.0.0 Safari/537.36")
CONSENT = re.compile(r"^(accept( all)?( cookies)?|i accept|agree|i agree|got it|ok(ay)?|allow all|consent|continue)$", re.I)


class FetchError(Exception):
    pass


@dataclass
class FetchResult:
    pdf_path: Path
    title: str | None
    body_text: str
    html: str
    draft: dict | None
    cover_png: bytes | None


def fetch_to_pdf(url: str, timeout_ms: int = 45000) -> FetchResult:
    from playwright.sync_api import Error as PWError, sync_playwright
    cfg = get_settings()
    tmp = cfg.library_dir / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    out = tmp / f"{uuid.uuid4().hex}.pdf"
    paper = "A4" if cfg.paper.lower() == "a4" else "Letter"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900}, locale="en-US",
                                      java_script_enabled=True)
            page = ctx.new_page()
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            except PWError as e:
                raise FetchError(f"could not load {url}: {e.message.splitlines()[0]}") from e
            status = resp.status if resp else 0
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except PWError:
                pass
            _dismiss_consent(page)
            _scroll(page)
            title = (page.title() or "").strip() or None
            hero = _screenshot_largest_image(page)
            html = page.content()
            try:
                body_text = page.inner_text("body")
            except PWError:
                body_text = ""
            if status >= 400:
                raise FetchError(f"{url} returned HTTP {status}" + (" (not found)" if status == 404 else ""))
            if re.search(r"^(just a moment|attention required|access denied)", title or "", re.I):
                raise FetchError(f"{url} is behind a bot check; print it from your browser to the Recipe Library printer instead")
            try:
                page.emulate_media(media="print")
            except PWError:
                pass
            page.pdf(path=str(out), format=paper, print_background=True,
                     margin={"top": "0.5in", "bottom": "0.5in", "left": "0.5in", "right": "0.5in"},
                     display_header_footer=True, footer_template=_footer(url), header_template="<span></span>")
        finally:
            browser.close()
    draft, image_url = scrape(html, url)
    cover = (_download_cover(image_url) if image_url else None) or hero
    if draft and draft.get("title"):
        title = draft["title"]
    return FetchResult(pdf_path=out, title=_clean_title(title), body_text=body_text, html=html, draft=draft, cover_png=cover)


def _footer(url: str) -> str:
    safe = url.replace("<", "&lt;").replace("&", "&amp;")
    return (f'<div style="font-size:8px;color:#666;width:100%;padding:0 0.5in;display:flex;justify-content:space-between">'
            f'<span>{safe}</span><span class="pageNumber"></span>/<span class="totalPages"></span></div>')


def _dismiss_consent(page) -> None:
    """Best-effort click on a cookie/consent button so it doesn't cover the recipe."""
    try:
        for btn in page.locator("button, [role=button], a.button").all()[:60]:
            try:
                txt = (btn.inner_text(timeout=300) or "").strip()
            except Exception:  # noqa: BLE001
                continue
            if CONSENT.match(txt) and btn.is_visible():
                btn.click(timeout=1500)
                page.wait_for_timeout(500)
                return
    except Exception:  # noqa: BLE001
        pass


def _scroll(page) -> None:
    """Scroll through the page so lazy-loaded images render into the PDF."""
    try:
        h = page.evaluate("document.body.scrollHeight")
        y = 0
        while y < min(h, 20000):
            y += 800
            page.evaluate(f"window.scrollTo(0, {y})")
            page.wait_for_timeout(120)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
    except Exception:  # noqa: BLE001
        pass


def _screenshot_largest_image(page) -> bytes | None:
    """A screenshot of the page's biggest visible photo, used when the site's
    own image URL is missing or refuses to download (hotlink protection)."""
    try:
        info = page.evaluate("""() => {
          let best = null, area = 0;
          for (const im of document.images) {
            const r = im.getBoundingClientRect();
            if (r.width < 240 || r.height < 160) continue;
            const a = r.width * r.height;
            if (a > area && r.width / r.height < 3 && r.height / r.width < 2) { area = a; best = im; }
          }
          if (!best) return null;
          best.scrollIntoView({block: 'center'});
          return true;
        }""")
        if not info:
            return None
        page.wait_for_timeout(300)
        handle = page.evaluate_handle("""() => {
          let best = null, area = 0;
          for (const im of document.images) { const r = im.getBoundingClientRect(); const a = r.width * r.height;
            if (r.width >= 240 && r.height >= 160 && a > area && r.width / r.height < 3 && r.height / r.width < 2) { area = a; best = im; } }
          return best;
        }""")
        el = handle.as_element()
        if el is None:
            return None
        png = el.screenshot(type="png")
        page.evaluate("window.scrollTo(0, 0)")
        return png if len(png) > 2000 else None
    except Exception:  # noqa: BLE001
        return None


def _clean_title(t: str | None) -> str | None:
    if not t:
        return None
    t = re.sub(r"\s*[|\-–—]\s*[^|\-–—]{2,40}$", "", t) if len(t) > 40 else t
    return t.strip()[:200] or None


# ---------------------------------------------------------------------------
# recipe-scrapers
# ---------------------------------------------------------------------------

def scrape(html: str, url: str) -> tuple[dict | None, str | None]:
    """Structured recipe from the page markup (schema.org, site-specific
    scrapers). Returns (draft, image url); draft is None when the page has
    no usable recipe data."""
    try:
        from recipe_scrapers import scrape_html
    except Exception:  # noqa: BLE001
        return None, None
    try:
        s = scrape_html(html, org_url=url, supported_only=False)
    except Exception as e:  # noqa: BLE001
        log.info("recipe-scrapers could not parse %s: %s", url, e)
        return None, None

    def get(name, default=None):
        try:
            v = getattr(s, name)()
            return v if v not in ("", [], None) else default
        except Exception:  # noqa: BLE001
            return default

    ingredients: list[dict] = []
    groups = get("ingredient_groups")
    if groups:
        for g in groups:
            purpose = getattr(g, "purpose", None)
            for line in getattr(g, "ingredients", []) or []:
                ingredients.append({"group": purpose or None, "raw": str(line).strip()})
    else:
        for line in get("ingredients", []) or []:
            ingredients.append({"group": None, "raw": str(line).strip()})
    steps_raw = get("instructions_list") or []
    if not steps_raw:
        txt = get("instructions") or ""
        steps_raw = [ln.strip() for ln in str(txt).split("\n") if ln.strip()]
    steps = [{"group": None, "text": str(t).strip()} for t in steps_raw if str(t).strip()]
    title = get("title")
    if not ingredients and not steps:
        return None, get("image")
    # the raw recipeYield ("1 loaf (10 slices)") beats the library's normalised "1 slice"
    yields = None
    try:
        raw_yield = s.schema.data.get("recipeYield")
        if isinstance(raw_yield, list):
            raw_yield = ", ".join(str(x) for x in raw_yield if x)
        yields = str(raw_yield).strip() if raw_yield else None
    except Exception:  # noqa: BLE001
        pass
    yields = yields or get("yields")
    servings = _servings_from_yield(yields)
    category = get("category")
    cuisine = get("cuisine")
    draft = {
        "is_recipe": True,
        "title": title,
        "description": get("description"),
        "ingredients": ingredients,
        "steps": steps,
        "prep_minutes": get("prep_time"),
        "cook_minutes": get("cook_time"),
        "total_minutes": get("total_time"),
        "servings": servings,
        "yield_text": str(yields) if yields else None,
        "cuisine": _first(cuisine),
        "course": _first(category),
        "language": get("language"),
        "confidence": 0.92 if (ingredients and steps) else 0.5,
        "method": "scraper",
    }
    return draft, get("image")


def _servings_from_yield(yields) -> float | None:
    if not yields:
        return None
    txt = str(yields).lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:-\s*\d+\s*)?serv", txt)
    if m:
        return float(m.group(1))
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", txt)]
    return max(nums) if nums else None


def _first(v) -> str | None:
    if not v:
        return None
    s = str(v).split(",")[0].strip().lower()
    return s[:40] or None


def _download_cover(image_url: str) -> bytes | None:
    try:
        import httpx
        from PIL import Image
        r = httpx.get(image_url, timeout=15, follow_redirects=True, headers={"User-Agent": UA})
        r.raise_for_status()
        im = Image.open(io.BytesIO(r.content)).convert("RGB")
        if im.width < 200 or im.height < 150:
            return None
        im.thumbnail((1000, 1000))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:  # noqa: BLE001
        log.info("cover download failed for %s: %s", image_url, e)
        return None
