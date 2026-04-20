#!/usr/bin/env python3
"""
Nykaa Fragrance Scraper
Scrapes all products from 135 pages of the Nykaa fragrance category.

Strategy:
  1. Launch a headless Chromium browser via Playwright.
  2. For each page, intercept XHR/Fetch responses to capture JSON from
     Nykaa's internal product API (fastest, most structured).
  3. Fall back to HTML parsing if no API data is intercepted.
  4. Save results incrementally to CSV so a crash loses minimal work.
  5. Skip already-scraped pages on re-run (resume support).

Usage:
    pip install playwright
    playwright install chromium
    python scraper.py
"""

import asyncio
import csv
import json
import re
import random
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL    = "https://www.nykaa.com/fragrance/c/53"
PARAMS      = "sort=popularity&search_redirection=True&formulation_filter=228729"
TOTAL_PAGES = 135
OUTPUT_FILE = "nykaa_fragrances.csv"

# Substrings that identify Nykaa's product-data API endpoints
API_PATTERNS = [
    "api/product/search",
    "product/list",
    "search/v2",
    "algoliaNet",
    "typesense",
]

CSV_FIELDS = [
    "name", "brand", "mrp", "price", "discount_percent",
    "volume_ml", "rating", "num_ratings", "num_reviews",
    "product_url", "image_url", "page_no",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_volume(text: str) -> str:
    """Return the first 'NNml' match found in text, else empty string."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:ml|ML|Ml|mL)", text or "")
    return m.group(0) if m else ""


def clean_price(s) -> str:
    s = str(s).replace(",", "").replace("₹", "").strip()
    m = re.search(r"\d+(?:\.\d+)?", s)
    return m.group(0) if m else ""


def clean_number(s) -> str:
    s = str(s).replace(",", "").strip()
    m = re.search(r"\d+", s)
    return m.group(0) if m else ""


# ── JSON parser (API intercept) ───────────────────────────────────────────────

def _find_product_lists(obj, depth: int = 0) -> list:
    """Recursively look for arrays whose items look like product dicts."""
    found = []
    if depth > 6:
        return found
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        sample = obj[0]
        if any(k in sample for k in ("name", "title", "productName", "brand", "brandName")):
            found.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            found.extend(_find_product_lists(v, depth + 1))
    return found


def parse_from_json(data: dict, page_num: int) -> list[dict]:
    candidates = _find_product_lists(data)
    if not candidates:
        return []

    items = max(candidates, key=len)
    products = []

    for item in items:
        name  = item.get("name") or item.get("title") or item.get("productName") or ""
        brand = item.get("brand") or item.get("brandName") or ""
        mrp   = item.get("mrp")  or item.get("mop") or item.get("originalPrice") or ""
        price = item.get("price") or item.get("discountedPrice") or item.get("offerPrice") or mrp
        disc  = item.get("discount") or item.get("discountPercent") or ""
        rat   = item.get("rating") or item.get("averageRating") or item.get("productRating") or ""
        nrat  = (
            item.get("ratingsCount") or item.get("ratingCount")
            or item.get("numRatings") or ""
        )
        nrev  = (
            item.get("reviewsCount") or item.get("reviewCount")
            or item.get("numReviews") or ""
        )

        slug = item.get("slug") or item.get("url") or item.get("productUrl") or ""
        product_url = (
            f"https://www.nykaa.com{slug}"
            if slug and not slug.startswith("http")
            else slug
        )

        imgs = item.get("images")
        if isinstance(imgs, list) and imgs:
            image_url = imgs[0].get("url", "") if isinstance(imgs[0], dict) else str(imgs[0])
        else:
            image_url = item.get("imageUrl") or item.get("image") or ""

        attr_str = str(item.get("attributes") or item.get("specifications") or "")
        volume   = extract_volume(str(name) + " " + attr_str)

        products.append({
            "name":             str(name),
            "brand":            str(brand),
            "mrp":              clean_price(mrp),
            "price":            clean_price(price),
            "discount_percent": str(disc).replace("%", "").strip(),
            "volume_ml":        volume,
            "rating":           str(rat),
            "num_ratings":      clean_number(nrat),
            "num_reviews":      clean_number(nrev),
            "product_url":      product_url,
            "image_url":        str(image_url),
            "page_no":          page_num,
        })

    return products


# ── HTML parser (fallback) ────────────────────────────────────────────────────

async def _get_text(el, selectors: list[str]) -> str:
    for sel in selectors:
        try:
            node = await el.query_selector(sel)
            if node:
                txt = await node.inner_text()
                if txt.strip():
                    return txt.strip()
        except Exception:
            pass
    return ""


async def parse_from_html(page, page_num: int) -> list[dict]:
    card_selectors = [
        ".product-list--item",
        "[data-testid='product-card']",
        "[class*='product-list']",
        "[class*='productList']",
        "[class*='product-card']",
        "[class*='ProductCard']",
        ".css-d5z3ro",
        ".product-item",
    ]

    cards = []
    for sel in card_selectors:
        try:
            cards = await page.query_selector_all(sel)
            if cards:
                print(f"    HTML fallback: {len(cards)} cards via '{sel}'")
                break
        except Exception:
            pass

    if not cards:
        print(f"    HTML fallback: no product cards found on page {page_num}")
        return []

    products = []
    for card in cards:
        try:
            name = await _get_text(card, [
                ".product-name", "[data-testid='product-title']",
                "[class*='productName']", "[class*='product-name']", "h3", "h2",
            ])
            brand = await _get_text(card, [
                ".brand-name", "[data-testid='product-brand']",
                "[class*='brand']", "[class*='Brand']",
            ])
            mrp = await _get_text(card, [
                ".product-mrp", "[data-testid='mrp']",
                "[class*='mrp']", "[class*='MRP']", "[class*='originalPrice']",
            ])
            price = await _get_text(card, [
                "[data-testid='offer-price']", ".offer-price",
                "[class*='offerPrice']", "[class*='offer-price']",
                "[class*='discountedPrice']", ".product-price",
            ])
            discount = await _get_text(card, [
                "[class*='discount']", "[class*='Discount']", ".discount",
            ])
            rating = await _get_text(card, [
                ".rating", "[data-testid='rating']",
                "[class*='rating']", "[class*='Rating']",
            ])
            num_ratings = await _get_text(card, [
                ".review-count", "[data-testid='reviews']",
                "[class*='ratingsCount']", "[class*='review']",
            ])

            img_el    = await card.query_selector("img")
            image_url = await img_el.get_attribute("src") if img_el else ""

            link_el = await card.query_selector("a")
            href    = await link_el.get_attribute("href") if link_el else ""
            product_url = (
                f"https://www.nykaa.com{href}"
                if href and href.startswith("/")
                else href
            )

            products.append({
                "name":             name,
                "brand":            brand,
                "mrp":              clean_price(mrp),
                "price":            clean_price(price or mrp),
                "discount_percent": discount.replace("%", "").strip(),
                "volume_ml":        extract_volume(name),
                "rating":           clean_number(rating),
                "num_ratings":      clean_number(num_ratings),
                "num_reviews":      "",
                "product_url":      product_url,
                "image_url":        image_url,
                "page_no":          page_num,
            })
        except Exception:
            continue

    return products


# ── Per-page scrape ───────────────────────────────────────────────────────────

async def scrape_page(browser, page_num: int) -> list[dict]:
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1366, "height": 768},
        locale="en-IN",
        extra_http_headers={
            "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/webp,*/*;q=0.8"
            ),
        },
    )

    page = await context.new_page()
    intercepted: list[dict] = []

    async def capture(response):
        if response.status != 200:
            return
        if not any(p in response.url for p in API_PATTERNS):
            return
        ct = response.headers.get("content-type", "")
        if "json" not in ct:
            return
        try:
            data = await response.json()
            intercepted.append(data)
        except Exception:
            pass

    page.on("response", capture)

    url = f"{BASE_URL}?page_no={page_num}&{PARAMS}"
    try:
        await page.goto(url, wait_until="networkidle", timeout=60_000)
    except PlaywrightTimeout:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            await page.wait_for_timeout(4_000)
        except Exception as e:
            print(f"    Page {page_num} failed to load: {e}")
            await context.close()
            return []

    # Extra settle time for JS-rendered content
    await page.wait_for_timeout(2_000)

    products: list[dict] = []

    # Try API data first
    for data in intercepted:
        parsed = parse_from_json(data, page_num)
        if parsed:
            products = parsed
            print(f"    API intercept: {len(products)} products")
            break

    # Fall back to HTML
    if not products:
        products = await parse_from_html(page, page_num)

    await context.close()
    return products


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    print(f"Nykaa Fragrance Scraper  |  {TOTAL_PAGES} pages  →  {OUTPUT_FILE}\n")

    output_path = Path(OUTPUT_FILE)
    scraped_pages: set[int] = set()
    all_products: list[dict] = []

    # Resume: load existing CSV
    if output_path.exists():
        with open(output_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                all_products.append(row)
                scraped_pages.add(int(row.get("page_no", 0)))
        print(
            f"Resuming — {len(scraped_pages)} pages done, "
            f"{len(all_products)} products already saved.\n"
        )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        for page_num in range(1, TOTAL_PAGES + 1):
            if page_num in scraped_pages:
                continue

            print(f"[{page_num:>3}/{TOTAL_PAGES}] Scraping page {page_num}...")

            for attempt in range(1, 4):
                try:
                    products = await scrape_page(browser, page_num)
                    all_products.extend(products)
                    print(
                        f"  ✓  {len(products):>3} products  "
                        f"| running total: {len(all_products)}"
                    )
                    break
                except Exception as e:
                    wait = 2 ** attempt
                    print(f"  Attempt {attempt} error: {e}  — retrying in {wait}s")
                    await asyncio.sleep(wait)
            else:
                print(f"  ✗  Page {page_num} skipped after 3 failures.")

            # Incremental save after every page
            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                writer.writeheader()
                writer.writerows(all_products)

            # Polite delay so we don't hammer Nykaa's servers
            await asyncio.sleep(random.uniform(2.0, 4.5))

        await browser.close()

    print(f"\nDone!  {len(all_products)} products saved to '{OUTPUT_FILE}'")


if __name__ == "__main__":
    asyncio.run(main())
