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

from playwright.async_api import async_playwright

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL    = "https://www.nykaa.com/fragrance/c/53"
PARAMS      = "sort=popularity&search_redirection=True&formulation_filter=228729"
TOTAL_PAGES = 135
OUTPUT_FILE = "nykaa_fragrances.csv"

CSV_FIELDS = [
    "name", "mrp", "price", "discount_percent",
    "volume_ml", "num_ratings", "product_url", "image_url", "page_no",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_volume(text: str) -> str:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:ml|ML|Ml|mL)", text or "")
    return m.group(0) if m else ""

def clean_price(s: str) -> str:
    s = str(s).replace(",", "").replace("₹", "").replace(".", "").strip()
    m = re.search(r"\d+", s)
    return m.group(0) if m else ""

def clean_number(s: str) -> str:
    s = str(s).replace(",", "").strip()
    m = re.search(r"\d+", s)
    return m.group(0) if m else ""

# ── Card text parser ──────────────────────────────────────────────────────────
# Observed format from debug:
#   "Brand+Name\n₹MRP\n₹Price\n10% Off\nRegular price ₹MRP. Discounted...\n( 274 )"
#   "Brand+Name (50ml)\nPrice ₹8355.\n₹8355\n\nOffer Available\n\n( 88 )"

def parse_card(href: str, raw_text: str, img: str, page_num: int) -> dict:
    product_url = (
        f"https://www.nykaa.com{href}" if href.startswith("/") else href
    )

    lines = [l.strip() for l in raw_text.split("\n") if l.strip()]

    name        = lines[0] if lines else ""
    prices      = []
    discount    = ""
    num_ratings = ""

    for line in lines[1:]:
        # "( 274 )" — review count
        if re.match(r"^\(\s*[\d,]+\s*\)$", line):
            num_ratings = clean_number(line)

        # "₹1099" — bare price
        elif re.match(r"^₹[\d,]+\.?$", line):
            prices.append(clean_price(line))

        # "Price ₹8355." — labeled price
        elif re.match(r"^Price\s+₹[\d,]+", line, re.I):
            m = re.search(r"₹([\d,]+)", line)
            if m:
                prices.append(m.group(1).replace(",", ""))

        # "10% Off"
        elif re.match(r"^\d+%\s*Off$", line, re.I):
            m = re.search(r"(\d+)%", line)
            if m:
                discount = m.group(1)

        # skip "Regular price ...", "Offer Available", etc.

    if len(prices) >= 2:
        mrp   = prices[0]
        price = prices[1]
    elif len(prices) == 1:
        mrp   = prices[0]
        price = prices[0]
    else:
        mrp = price = ""

    return {
        "name":             name,
        "mrp":              mrp,
        "price":            price,
        "discount_percent": discount,
        "volume_ml":        extract_volume(name),
        "num_ratings":      num_ratings,
        "product_url":      product_url,
        "image_url":        img,
        "page_no":          page_num,
    }

# ── JS extractor (runs inside browser) ───────────────────────────────────────
# Deduplicates by productId in the URL query string.

JS_EXTRACT = """
() => {
    const seen = new Set();
    const out  = [];

    for (const a of document.querySelectorAll('a[href*="/p/"]')) {
        const href = a.getAttribute('href') || '';
        if (!href) continue;

        // Use productId as unique key so image-link and text-link collapse to one
        const m = href.match(/productId=(\d+)/);
        const key = m ? m[1] : href;
        if (seen.has(key)) continue;
        seen.add(key);

        const text = a.innerText || '';
        const img  = a.querySelector('img');
        const src  = img ? (img.src || img.getAttribute('data-src') || '') : '';

        out.push({ href, text, img: src });
    }
    return out;
}
"""

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
        },
    )

    page = await context.new_page()

    url = f"{BASE_URL}?page_no={page_num}&{PARAMS}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    except Exception as e:
        print(f"    Page {page_num} failed to load: {e}")
        await context.close()
        return []

    await page.wait_for_timeout(3_000)

    # Scroll to trigger lazy-loaded products
    for _ in range(8):
        await page.evaluate("window.scrollBy(0, window.innerHeight)")
        await page.wait_for_timeout(600)
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await page.wait_for_timeout(2_000)

    # Extract all product cards via JS
    cards = await page.evaluate(JS_EXTRACT)

    products = [
        parse_card(c["href"], c["text"], c["img"], page_num)
        for c in cards
        if c["text"].strip()
    ]

    print(f"    {len(products)} products found")
    await context.close()
    return products

# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    print(f"Nykaa Fragrance Scraper  |  {TOTAL_PAGES} pages  →  {OUTPUT_FILE}\n")

    output_path   = Path(OUTPUT_FILE)
    scraped_pages: set[int] = set()
    all_products:  list[dict] = []

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

            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                writer.writeheader()
                writer.writerows(all_products)

            await asyncio.sleep(random.uniform(2.0, 4.5))

        await browser.close()

    print(f"\nDone!  {len(all_products)} products saved to '{OUTPUT_FILE}'")


if __name__ == "__main__":
    asyncio.run(main())
