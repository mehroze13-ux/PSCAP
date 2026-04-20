"""
scraper_runner.py
Scrapes Amazon India reviews for every product in products.csv.
One Chrome window stays open for all products — log in once, it handles the rest.
"""

import csv
import os
import random
import sys
import time
import pymysql
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

load_dotenv(".env")

# ── Paste your scraper helpers here (scrape_reviews_for_asin, scrape_product_rating)
# ── from the original scraper.py that came with the repo
from scraper import scrape_reviews_for_asin, scrape_product_rating

# ── Config ──────────────────────────────────────────────────────────────────────

PRODUCTS_CSV      = os.path.join(os.path.dirname(__file__), "products.csv")
CHROMEDRIVER_PATH = os.getenv("CHROMEDRIVER_PATH", "")
PAUSE_SECONDS     = float(os.getenv("SCRAPER_PAUSE", "8"))
CHROME_PROFILE    = os.getenv("CHROME_PROFILE", os.path.join(os.path.dirname(__file__), "chrome-profile"))
os.makedirs(CHROME_PROFILE, exist_ok=True)

_filter_raw  = os.getenv("SCRAPE_ASINS", "").strip()
ASIN_FILTER  = set(a.strip() for a in _filter_raw.split(",") if a.strip())

# ── Database ────────────────────────────────────────────────────────────────────

def get_db():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=3306,
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        charset="utf8mb4",
        autocommit=False,
    )

def insert_reviews(reviews: list, conn) -> int:
    cur = conn.cursor()
    inserted = 0
    for r in reviews:
        cur.execute(
            """
            INSERT IGNORE INTO raw_reviews
            (review_id, asin, product_name, category, rating, title,
             review, review_date, review_url, scrape_date)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                r["review_id"], r["asin"], r["product_name"],
                r.get("category", ""),
                r["rating"], r["title"], r["review"],
                r["review_date"], r["review_url"], r["scrape_date"],
            ),
        )
        inserted += cur.rowcount
    conn.commit()
    cur.close()
    return inserted

def insert_rating_snapshot(asin, product_name, snapshot, conn):
    if not snapshot or not snapshot.get("overall_rating"):
        return
    cur = conn.cursor()
    cur.execute(
        """
        INSERT IGNORE INTO product_ratings_snapshot
        (asin, product_name, scraped_date, overall_rating, total_ratings)
        VALUES (%s, %s, CURDATE(), %s, %s)
        """,
        (asin, product_name, snapshot["overall_rating"], snapshot.get("total_ratings")),
    )
    conn.commit()
    cur.close()

# ── Browser ─────────────────────────────────────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

def make_driver():
    options = Options()
    options.add_argument(f"--user-agent={random.choice(USER_AGENTS)}")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument(f"--user-data-dir={CHROME_PROFILE}")

    driver = webdriver.Chrome(
        service=Service(CHROMEDRIVER_PATH) if CHROMEDRIVER_PATH else None,
        options=options,
    )
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = { runtime: {} };
    """})
    return driver

# ── Login / page detection ───────────────────────────────────────────────────────

def needs_login(driver):
    if any(k in driver.current_url.lower() for k in ("ap/signin", "sign-in")):
        return True
    try:
        return bool(driver.execute_script(
            "return !!(document.querySelector('input#ap_email') || "
            "document.querySelector('form[name=\"signIn\"]'));"
        ))
    except Exception:
        return False

def reviews_visible(driver):
    try:
        return bool(driver.execute_script(
            "return document.querySelectorAll('[data-hook=\"review\"]').length > 0;"
        ))
    except Exception:
        return False

def wait_for_reviews(driver, asin, timeout=300):
    url = f"https://www.amazon.in/product-reviews/{asin}?sortBy=recent"
    driver.get(url)
    login_warned = False
    elapsed = 0
    while elapsed < timeout:
        time.sleep(2)
        elapsed += 2
        if reviews_visible(driver):
            return
        if needs_login(driver):
            if not login_warned:
                print("\n" + "="*50)
                print("Amazon wants you to log in.")
                print("Log in in the browser — this script will continue.")
                print("="*50)
                login_warned = True
    raise RuntimeError(f"Timed out ({timeout}s) waiting for reviews. URL: {driver.current_url}")

# ── Scrape one product ───────────────────────────────────────────────────────────

def scrape_product(row, driver, db_conn):
    asin, name, category = row["asin"], row["product_name"], row.get("category", "")
    try:
        wait_for_reviews(driver, asin)
        reviews  = scrape_reviews_for_asin(driver, asin, name, category=category, already_on_page=True)
        inserted = insert_reviews(reviews, db_conn)
        print(f"  {len(reviews)} scraped, {inserted} new rows saved")

        snapshot = scrape_product_rating(driver, asin)
        if snapshot:
            insert_rating_snapshot(asin, name, snapshot, db_conn)
            print(f"  Rating: {snapshot['overall_rating']}★")

        return {"asin": asin, "name": name, "ok": True}
    except Exception as e:
        print(f"  ERROR: {e}")
        return {"asin": asin, "name": name, "ok": False, "error": str(e)}

# ── Main ─────────────────────────────────────────────────────────────────────────

def main():
    with open(PRODUCTS_CSV, newline="", encoding="utf-8") as f:
        products = [
            row for row in csv.DictReader(f)
            if not ASIN_FILTER or row["asin"] in ASIN_FILTER
        ]

    if not products:
        print("No products to scrape. Check products.csv and SCRAPE_ASINS in .env")
        return

    print(f"\nScraping {len(products)} product(s) — estimated {len(products)*3} min\n")

    db   = get_db()
    drv  = make_driver()
    results = []

    try:
        for i, row in enumerate(products, 1):
            print(f"[{i}/{len(products)}] {row['product_name']} ({row['asin']})")
            results.append(scrape_product(row, drv, db))
            if i < len(products):
                pause = random.uniform(PAUSE_SECONDS * 0.8, PAUSE_SECONDS * 1.2)
                print(f"  Pausing {pause:.0f}s...")
                time.sleep(pause)
    finally:
        drv.quit()
        db.close()

    failed = [r for r in results if not r.get("ok")]
    print(f"\nDone — {len(results) - len(failed)}/{len(results)} succeeded")
    if failed:
        for f in failed:
            print(f"  FAILED: {f['asin']} — {f.get('error')}")

if __name__ == "__main__":
    main()
