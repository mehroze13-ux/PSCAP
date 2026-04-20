#!/usr/bin/env python3
"""
Nykaa Fragrance Scraper
Scrapes all products from 135 pages of the Nykaa fragrance category.

Usage:
    pip install playwright
    python -m playwright install chromium
    python scraper.py
"""

import asyncio
import csv
import re
import random
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL    = "https://www.nykaa.com/fragrance/c/53"
PARAMS      = "sort=popularity&search_redirection=True&formulation_filter=228729"
TOTAL_PAGES = 135
OUTPUT_FILE = "nykaa_fragrances.csv"

CSV_FIELDS = [
    "name", "brand", "mrp", "price", "discount_percent",
    "volume_ml", "rating", "num_ratings", "num_reviews",
    "product_url", "image_url", "page_no",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_volume(text: str) -> str:
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

# ── JSON parser ───────────────────────────────────────────────────────────────

def _find_product_lists(obj, depth: int = 0) -> list:
    found = []
    if depth > 8:
        return found
    if isinstance(obj, list) and len(obj) > 1 and isinstance(obj[0], dict):
        sample = obj[0]
        if any(k in sample for k in ("name", "title", "productName", "brand", "brandName", "sku")):
            found.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            found.extend(_find_product_lists(v, depth + 1))
    return found

def parse_from_json(data, page_num: int) -> list[dict]:
    candidates = _find_product_lists(data)
    if not candidates:
        return []

    # Pick the largest list — most likely the main product listing
    items = max(candidates, key=len)
    products = []

    for item in items:
        name  = item.get("name") or item.get("title") or item.get("productName") or ""
        brand = item.get("brand") or item.get("brandName") or ""
        mrp   = item.get("mrp") or item.get("mop") or item.get("originalPrice") or ""
        price = item.get("price") or item.get("discountedPrice") or item.get("offerPrice") or mrp
        disc  = item.get("discount") or item.get("discountPercent") or ""
        rat   = item.get("rating") or item.get("averageRating") or item.get("productRating") or ""
        nrat  = item.get("ratingsCount") or item.get("ratingCount") or item.get("numRatings") or ""
        nrev  = item.get("reviewsCount") or item.get("reviewCount") or item.get("numReviews") or ""

        slug = item.get("slug") or item.get("url") or item.get("productUrl") or ""
        product_url = (
            f"https://www.nykaa.com{slug}"
            if slug and not slug.startswith("http") else slug
        )

        imgs = item.get("images")
        if isinstance(imgs, list) and imgs:
            image_url = imgs[0].get("url", "") if isinstance(imgs[0], dict) else str(imgs[0])
        else:
            image_url = item.get("imageUrl") or item.get("image") or ""

        attr_str = str(item.get("attributes") or item.get("specifications") or "")
        volume = extract_volume(str(name) + " " + attr_str)

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

async def parse_from_html(page, page_num: int) -> list[dict]:
    """
    Find all product links on the page (href matches Nykaa product URL pattern),
    then extract data from each card's surrounding container.
    """
    # All anchor tags linking to product pages
    links = await page.query_selector_all("a[href*='/p/']")
    if not links:
        links = await page.query_selector_all("a[href*='nykaa.com']")

    seen_urls = set()
    products = []

    for link in links:
        try:
            href = await link.get_attribute("href") or ""
            if not href or href in seen_urls:
                continue
            # Only product detail pages
            if "/p/" not in href and not re.search(r"/\d+$", href):
                continue
            seen_urls.add(href)

            product_url = (
                f"https://www.nykaa.com{href}"
                if href.startswith("/") else href
            )

            # Get all text content from the card
            card_text = await link.inner_text()
            lines = [l.strip() for l in card_text.splitlines() if l.strip()]

            # Image
            img = await link.query_selector("img")
            image_url = ""
            if img:
                image_url = (
                    await img.get_attribute("src") or
                    await img.get_attribute("data-src") or ""
                )

            # Parse lines heuristically
            name = lines[0] if lines else ""
            brand = lines[1] if len(lines) > 1 else ""
            price = ""
            mrp = ""
            rating = ""
            num_ratings = ""

            for line in lines:
                if re.match(r"^[₹\d]", line) and not price:
                    price = clean_price(line)
                elif re.match(r"^\d+(\.\d+)?$", line) and float(line.replace(",","")) <= 5:
                    rating = line
                elif re.search(r"\d+\s*(ratings?|reviews?|K\s*ratings?)", line, re.I):
                    num_ratings = clean_number(line)
                elif "MRP" in line.upper():
                    mrp = clean_price(line)

            products.append({
                "name":             name,
                "brand":            brand,
                "mrp":              mrp or price,
                "price":            price,
                "discount_percent": "",
                "volume_ml":        extract_volume(name),
                "rating":           rating,
                "num_ratings":      num_ratings,
                "num_reviews":      "",
                "product_url":      product_url,
                "image_url":        image_url,
                "page_no":          page_num,
            })
        except Exception:
            continue

    print(f"    HTML fallback: {len(products)} products")
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
        ignore_https_errors=True,
        extra_http_headers={
            "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Upgrade-Insecure-Requests": "1",
        },
    )

    page = await context.new_page()
    intercepted: list[dict] = []

    # Intercept ALL JSON responses and check if they contain product data
    async def capture(response):
        if response.status != 200:
            return
        ct = response.headers.get("content-type", "")
        if "json" not in ct:
            return
        try:
            data = await response.json()
            if _find_product_lists(data):
                intercepted.append(data)
        except Exception:
            pass

    page.on("response", capture)

    url = f"{BASE_URL}?page_no={page_num}&{PARAMS}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    except Exception as e:
        print(f"    Page {page_num} failed to load: {e}")
        await context.close()
        return []

    await page.wait_for_timeout(3_000)

    # Scroll down to trigger lazy-loaded products
    for _ in range(5):
        await page.evaluate("window.scrollBy(0, window.innerHeight)")
        await page.wait_for_timeout(800)
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await page.wait_for_timeout(2_000)

    products: list[dict] = []

    # Pick the intercepted response with the most products
    best = []
    for data in intercepted:
        parsed = parse_from_json(data, page_num)
        if len(parsed) > len(best):
            best = parsed

    if best:
        products = best
        print(f"    API intercept: {len(products)} products")
    else:
        products = await parse_from_html(page, page_num)

    await context.close()
    return products

# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    print(f"Nykaa Fragrance Scraper  |  {TOTAL_PAGES} pages  →  {OUTPUT_FILE}\n")

    output_path = Path(OUTPUT_FILE)
    scraped_pages: set[int] = set()
    all_products: list[dict] = []

    if output_path.exists():
        with open(output_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                all_products.append(row)
                scraped_pages.add(int(row.get("page_no", 0)))
        print(f"Resuming — {len(scraped_pages)} pages done, {len(all_products)} products loaded.\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-http2",
                "--disable-web-security",
                "--allow-running-insecure-content",
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
                    print(f"  ✓  {len(products):>3} products | total: {len(all_products)}")
                    break
                except Exception as e:
                    wait = 2 ** attempt
                    print(f"  Attempt {attempt} error: {e} — retrying in {wait}s")
                    await asyncio.sleep(wait)
            else:
                print(f"  ✗  Page {page_num} skipped after 3 failures.")

            # Save after every page
            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                writer.writeheader()
                writer.writerows(all_products)

            await asyncio.sleep(random.uniform(2.0, 4.5))

        await browser.close()

    print(f"\nDone!  {len(all_products)} products saved to '{OUTPUT_FILE}'")


if __name__ == "__main__":
    asyncio.run(main())
