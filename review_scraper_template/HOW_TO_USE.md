# Amazon Review Scraper — Quick Start Guide

## What this does
1. **Scrapes** reviews from Amazon India for products you list
2. **Tags** each review with AI (sentiment + categories)
3. **Stores** everything in MySQL for the dashboard to read

---

## Step 1 — One-time setup

### Install dependencies
```
pip install selenium pymysql openai python-dotenv
```

### Set up the database
```
mysql -u root -p < setup.sql
```

### Configure your environment
```
cp .env.template .env
```
Then open `.env` and fill in:
- `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`
- `OPENAI_API_KEY`
- `CHROMEDRIVER_PATH` (path to your chromedriver)

---

## Step 2 — Add your products

Open `products.csv` and add one row per product:

```
asin,product_name,category
B09XXXXX,boAt Rockerz 450,Headphones
B08YYYYYY,Noise Colorfit Pro,Smartwatch
```

**Where to find the ASIN:**
Go to the product page on amazon.in — it's in the URL:
`amazon.in/dp/B09XXXXX` → ASIN is `B09XXXXX`

---

## Step 3 — Define your categories

Open `taxonomy.json` and set categories that make sense for your products:

```json
{
  "Headphones": {
    "subcategories": ["Sound Quality", "Comfort", "Battery Life", "Build Quality", "Value for Money"]
  },
  "Smartwatch": {
    "subcategories": ["Display", "Fitness Tracking", "Battery Life", "App Support", "Design"]
  }
}
```

---

## Step 4 — Run

```
python run_pipeline.py
```

A Chrome window will open. If Amazon asks you to log in, do it manually — the script waits and then continues automatically.

---

## Scrape specific products only

To scrape just one or two products without editing the CSV:
```
SCRAPE_ASINS=B09XXXXX,B08YYYYYY python run_pipeline.py
```

---

## Files overview

| File | What to edit |
|------|-------------|
| `products.csv` | Your product ASINs and names |
| `taxonomy.json` | Categories for AI tagging |
| `.env` | DB credentials, API keys, paths |
| `scraper_runner.py` | Scraping logic (usually no changes needed) |
| `tagger.py` | Tagging logic (usually no changes needed) |
| `run_pipeline.py` | Runs both steps in sequence |
| `setup.sql` | Run once to create DB tables |
