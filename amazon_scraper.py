#!/usr/bin/env python3
"""
Amazon India Fragrance Bestsellers Scraper
Scrapes 2 pages (~50 products each) from the Amazon bestsellers list.

Usage:
    pip install playwright
    python -m playwright install chromium
    python amazon_scraper.py
"""

import asyncio
import csv
import re
import random
from pathlib import Path

from playwright.async_api import async_playwright

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL    = "https://www.amazon.in/gp/bestsellers/beauty/1374300031"
TOTAL_PAGES = 2
OUTPUT_FILE = "amazon_fragrances.csv"

CSV_FIELDS = [
    "rank", "name", "price", "rating", "num_ratings",
    "asin", "product_url", "image_url", "page_no",
]

# ── JS extractor (runs inside browser) ───────────────────────────────────────

JS_EXTRACT = """
() => {
    const results = [];

    const items = document.querySelectorAll('[id^="gridItemRoot"]');

    for (const item of items) {

        // Rank
        const rankEl = item.querySelector('.zg-bdg-text');
        const rank = rankEl ? rankEl.textContent.trim() : '';

        // Product link + ASIN
        const linkEl = item.querySelector('a[href*="/dp/"]');
        const href   = linkEl ? linkEl.getAttribute('href') : '';
        const asinM  = href.match(/\\/dp\\/([A-Z0-9]{10})/);
        const asin   = asinM ? asinM[1] : '';
        const productUrl = href
            ? (href.startsWith('http') ? href : 'https://www.amazon.in' + href.split('?')[0])
            : '';

        // Image
        const imgEl = item.querySelector('img');
        const img   = imgEl ? (imgEl.src || imgEl.getAttribute('data-src') || '') : '';

        // Name — try known Amazon bestseller selectors
        const nameSelectors = [
            '._cDEzb_p13n-sc-css-line-clamp-3_g3dy1',
            '._cDEzb_p13n-sc-css-line-clamp-4_2q2cc',
            '.p13n-sc-truncate-desktop-type2',
            '.p13n-sc-truncate',
            '[class*="line-clamp"]',
            '[class*="truncate"]',
        ];
        let name = '';
        for (const sel of nameSelectors) {
            const el = item.querySelector(sel);
            if (el && el.textContent.trim()) {
                name = el.textContent.trim();
                break;
            }
        }
        // Last resort: all text inside the card minus rank
        if (!name) {
            const clone = item.cloneNode(true);
            const bdg   = clone.querySelector('.zg-bdg-text');
            if (bdg) bdg.remove();
            const allText = clone.innerText || clone.textContent || '';
            name = allText.split('\\n').map(s => s.trim()).find(s => s.length > 5) || '';
        }

        // Price
        const priceEl  = item.querySelector('.p13n-sc-price');
        const priceRaw = priceEl ? priceEl.textContent.trim() : '';
        const priceM   = priceRaw.match(/[\\d,]+/);
        const price    = priceM ? priceM[0].replace(/,/g, '') : '';

        // Rating  e.g. "4.3 out of 5 stars"
        const ratingEl   = item.querySelector('.a-icon-alt');
        const ratingText = ratingEl ? ratingEl.textContent.trim() : '';
        const ratingM    = ratingText.match(/([\\d.]+)\\s*out of/i);
        const rating     = ratingM ? ratingM[1] : '';

        // Review count — first element whose text is purely digits/commas
        let numRatings = '';
        for (const el of item.querySelectorAll('.a-size-small, .a-link-normal span')) {
            const t = el.textContent.trim().replace(/,/g, '');
            if (/^\\d+$/.test(t) && parseInt(t) > 0) {
                numRatings = t;
                break;
            }
        }

        if (name || asin) {
            results.push({ rank, name, price, rating, numRatings, asin, productUrl, img });
        }
    }

    return results;
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
        viewport={"width": 1440, "height": 900},
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

    ref = f"zg_bs_pg_{page_num}_beauty"
    url = f"{BASE_URL}/ref={ref}?ie=UTF8&pg={page_num}"

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    except Exception as e:
        print(f"    Page {page_num} failed to load: {e}")
        await context.close()
        return []

    await page.wait_for_timeout(3_000)

    # Scroll to load all lazy images / items
    for _ in range(6):
        await page.evaluate("window.scrollBy(0, window.innerHeight)")
        await page.wait_for_timeout(600)
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await page.wait_for_timeout(2_000)

    cards = await page.evaluate(JS_EXTRACT)

    products = []
    for c in cards:
        products.append({
            "rank":        c.get("rank", ""),
            "name":        c.get("name", ""),
            "price":       c.get("price", ""),
            "rating":      c.get("rating", ""),
            "num_ratings": c.get("numRatings", ""),
            "asin":        c.get("asin", ""),
            "product_url": c.get("productUrl", ""),
            "image_url":   c.get("img", ""),
            "page_no":     page_num,
        })

    print(f"    {len(products)} products found")
    await context.close()
    return products

# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    print(f"Amazon Fragrance Bestsellers Scraper  |  {TOTAL_PAGES} pages  →  {OUTPUT_FILE}\n")

    output_path  = Path(OUTPUT_FILE)
    all_products: list[dict] = []

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
            print(f"[{page_num}/{TOTAL_PAGES}] Scraping page {page_num}...")

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

            await asyncio.sleep(random.uniform(2.0, 4.0))

        await browser.close()

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(all_products)

    print(f"\nDone!  {len(all_products)} products saved to '{OUTPUT_FILE}'")


if __name__ == "__main__":
    asyncio.run(main())
