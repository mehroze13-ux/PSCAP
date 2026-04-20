#!/usr/bin/env python3
"""
Debug script - opens page 1, saves HTML and all JSON responses.
Run this and share the output files so we can fix the scraper.

Usage: python debug.py
"""

import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

URL = "https://www.nykaa.com/fragrance/c/53?page_no=1&sort=popularity&search_redirection=True&formulation_filter=228729"

async def main():
    print("Opening page... (browser will be VISIBLE so you can see it)")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,   # visible browser
            args=[
                "--no-sandbox",
                "--disable-http2",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="en-IN",
            ignore_https_errors=True,
        )

        page = await context.new_page()
        all_json = []

        async def capture(response):
            if response.status != 200:
                return
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                return
            try:
                data = await response.json()
                all_json.append({"url": response.url, "data": data})
            except Exception:
                pass

        page.on("response", capture)

        print(f"Navigating to {URL}")
        await page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(3_000)

        print("Scrolling...")
        for _ in range(8):
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await page.wait_for_timeout(800)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(2_000)

        # Save full HTML
        html = await page.content()
        Path("debug_page.html").write_text(html, encoding="utf-8")
        print(f"Saved HTML → debug_page.html ({len(html):,} bytes)")

        # Save all JSON responses
        Path("debug_responses.json").write_text(
            json.dumps(all_json, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"Saved {len(all_json)} JSON responses → debug_responses.json")

        # Check __NEXT_DATA__
        next_data = await page.evaluate(
            "() => { try { return document.getElementById('__NEXT_DATA__')?.textContent || ''; } catch(e) { return ''; } }"
        )
        if next_data:
            Path("debug_next_data.json").write_text(next_data, encoding="utf-8")
            print(f"Saved __NEXT_DATA__ → debug_next_data.json ({len(next_data):,} bytes)")
        else:
            print("No __NEXT_DATA__ found on this page.")

        # Count product links
        links = await page.query_selector_all("a[href*='/p/']")
        print(f"Found {len(links)} product links (a[href*='/p/'])")

        # Sample first product card texts
        if links:
            print("\n--- First product card texts ---")
            for i, link in enumerate(links[:3]):
                href = await link.get_attribute("href")
                txt = await link.inner_text()
                print(f"\nCard {i+1}: {href}")
                print(repr(txt[:300]))

        print("\nDone! Share the output above + the 3 debug files.")
        await page.wait_for_timeout(3_000)
        await browser.close()

asyncio.run(main())
