#!/usr/bin/env python3
"""
Nykaa Product Detail Scraper
Reads product URLs from the existing CSV and scrapes full details
from each product page using 10 parallel workers.

Usage:
    python detail_scraper.py
    python detail_scraper.py --input nykaa_fragrances.csv  (custom input file)
"""

import asyncio
import csv
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright

# ── Config ────────────────────────────────────────────────────────────────────

INPUT_FILE  = "nykaa_fragrances.csv"   # CSV with product_url column
OUTPUT_FILE = "nykaa_fragrances_full.csv"
CONCURRENCY = 5                         # parallel browser pages
URL_COLUMN  = "product_url"

CSV_FIELDS = [
    "name", "brand", "mrp", "price", "discount_percent",
    "volume_ml", "rating", "num_ratings", "num_reviews",
    "product_url", "image_url",
]

# ── JS extractor for product detail page ─────────────────────────────────────

JS_PRODUCT = """
() => {
    const getText = (selectors) => {
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el && el.textContent.trim()) return el.textContent.trim();
        }
        return '';
    };

    const getAttr = (selectors, attr) => {
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el && el.getAttribute(attr)) return el.getAttribute(attr);
        }
        return '';
    };

    // Product name
    const name = getText([
        'h1',
        '[class*="product-title"]',
        '[class*="productTitle"]',
        '[class*="pdp-title"]',
        '[data-testid="product-title"]',
    ]);

    // Brand
    const brand = getText([
        '[class*="brand-name"]',
        '[class*="brandName"]',
        'a[href*="/brand/"]',
        'a[href*="/brands/"]',
        '[data-testid="brand-name"]',
        '[class*="product-brand"]',
    ]);

    // Offer / discounted price
    const offerPrice = getText([
        '[class*="offer-price"]',
        '[class*="offerPrice"]',
        '[class*="selling-price"]',
        '[data-testid="offer-price"]',
        '[class*="final-price"]',
    ]);

    // MRP / base price
    const mrp = getText([
        '[class*="base-price"]',
        '[class*="basePrice"]',
        '[class*="mrp"]',
        '[class*="MRP"]',
        '[data-testid="mrp"]',
        '[class*="regular-price"]',
    ]);

    // Discount
    const discount = getText([
        '[class*="discount"]',
        '[class*="Discount"]',
        '[data-testid="discount"]',
    ]);

    // Rating
    const rating = getText([
        '[class*="avg-rating"]',
        '[class*="avgRating"]',
        '[class*="average-rating"]',
        '[data-testid="rating"]',
        '.rating-value',
        '[class*="product-rating"]',
    ]);

    // Number of ratings
    const numRatings = getText([
        '[class*="rating-count"]',
        '[class*="ratingCount"]',
        '[class*="ratings-count"]',
        '[data-testid="ratings-count"]',
        '[class*="num-ratings"]',
    ]);

    // Number of reviews
    const numReviews = getText([
        '[class*="review-count"]',
        '[class*="reviewCount"]',
        '[class*="num-reviews"]',
        '[data-testid="reviews-count"]',
    ]);

    // Image
    const image = getAttr([
        '[class*="product-image"] img',
        '[class*="productImage"] img',
        '.pdp-image img',
        '[data-testid="product-image"] img',
        'img[class*="product"]',
    ], 'src') || getAttr(['img[alt][src*="nykaa"]'], 'src');

    return { name, brand, offerPrice, mrp, discount, rating, numRatings, numReviews, image };
}
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_volume(text: str) -> str:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:ml|ML|Ml|mL)", text or "")
    return m.group(0) if m else ""

def clean_price(s: str) -> str:
    s = str(s).replace(",", "").replace("₹", "").strip()
    m = re.search(r"\d+(?:\.\d+)?", s)
    return m.group(0) if m else ""

def clean_number(s: str) -> str:
    s = str(s).replace(",", "").strip()
    m_k = re.match(r"([\d.]+)\s*k", s, re.I)
    if m_k:
        return str(int(float(m_k.group(1)) * 1000))
    m = re.search(r"\d+", s)
    return m.group(0) if m else ""

def clean_discount(s: str) -> str:
    m = re.search(r"(\d+)%", s or "")
    return m.group(1) if m else ""

# ── Single product scrape ─────────────────────────────────────────────────────

async def scrape_product(browser, sem: asyncio.Semaphore, url: str) -> dict:
    async with sem:
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
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        page = await context.new_page()

        result = {f: "" for f in CSV_FIELDS}
        result["product_url"] = url

        for attempt in range(1, 4):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(2_000)

                d = await page.evaluate(JS_PRODUCT)

                name  = d.get("name", "")
                brand = d.get("brand", "")
                price = clean_price(d.get("offerPrice", ""))
                mrp   = clean_price(d.get("mrp", "")) or price
                disc  = clean_discount(d.get("discount", ""))
                rat   = d.get("rating", "").replace("★", "").strip()
                nrat  = clean_number(d.get("numRatings", ""))
                nrev  = clean_number(d.get("numReviews", ""))
                img   = d.get("image", "")
                vol   = extract_volume(name)

                result.update({
                    "name":             name,
                    "brand":            brand,
                    "mrp":              mrp,
                    "price":            price,
                    "discount_percent": disc,
                    "volume_ml":        vol,
                    "rating":           rat,
                    "num_ratings":      nrat,
                    "num_reviews":      nrev,
                    "image_url":        img,
                })
                break

            except Exception as e:
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)
                else:
                    print(f"  FAILED: {url}  ({e})")

        await context.close()
        return result

# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    input_file  = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--input" else INPUT_FILE
    output_path = Path(OUTPUT_FILE)

    # Load URLs from input CSV
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"ERROR: {input_file} not found. Run scraper.py first.")
        return

    all_urls: list[str] = []
    with open(input_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            url = row.get(URL_COLUMN, "").strip()
            if url:
                all_urls.append(url)

    print(f"Loaded {len(all_urls)} URLs from {input_file}")

    # Resume: skip already-scraped URLs
    done_urls: set[str] = set()
    existing: list[dict] = []
    if output_path.exists():
        with open(output_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing.append(row)
                done_urls.add(row.get("product_url", ""))
        print(f"Resuming — {len(done_urls)} already done, {len(all_urls) - len(done_urls)} remaining\n")

    pending = [u for u in all_urls if u not in done_urls]
    all_results = list(existing)

    if not pending:
        print("All URLs already scraped!")
        return

    sem = asyncio.Semaphore(CONCURRENCY)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-http2",
            ],
        )

        # Process in batches of 50 so we save progress regularly
        batch_size = 50
        total = len(pending)

        for batch_start in range(0, total, batch_size):
            batch = pending[batch_start: batch_start + batch_size]
            batch_end = min(batch_start + batch_size, total)

            print(f"[{batch_start + 1}–{batch_end} / {total}] scraping...")

            tasks = []
            for i, url in enumerate(batch):
                await asyncio.sleep(i * 0.3)   # stagger start times
                tasks.append(asyncio.create_task(scrape_product(browser, sem, url)))
            results = await asyncio.gather(*tasks)

            all_results.extend(results)
            done_count = len(all_results)

            # Save after every batch
            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                writer.writeheader()
                writer.writerows(all_results)

            print(f"  ✓ saved {done_count} / {len(all_urls)} total")

        await browser.close()

    print(f"\nDone!  {len(all_results)} products saved to '{OUTPUT_FILE}'")


if __name__ == "__main__":
    asyncio.run(main())
