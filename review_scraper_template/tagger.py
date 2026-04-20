"""
tagger.py
Reads untagged reviews from the DB, sends them to Claude in batches,
and saves sentiment + category tags.

Taxonomy is loaded from taxonomy.json — edit that file to match your products.
"""

import json
import os
import pymysql
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv(".env")

# ── Config ──────────────────────────────────────────────────────────────────────

BATCH_SIZE  = 5
MODEL       = "gemini-1.5-flash"
RETAG_ALL   = os.getenv("RETAG_ALL_REVIEWS_ON_PIPELINE", "").strip().lower() in {"1", "true", "yes"}

TAXONOMY_PATH = os.path.join(os.path.dirname(__file__), "taxonomy.json")
with open(TAXONOMY_PATH, encoding="utf-8") as f:
    TAXONOMY = json.load(f)

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise RuntimeError("GEMINI_API_KEY not set in .env")

genai.configure(api_key=api_key)
client = genai.GenerativeModel(MODEL)

# ── Database ────────────────────────────────────────────────────────────────────

conn = pymysql.connect(
    host=os.getenv("DB_HOST", "localhost"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    database=os.getenv("DB_NAME"),
    charset="utf8mb4",
)
cur = conn.cursor()

if RETAG_ALL:
    print("RETAG_ALL enabled — clearing existing tags")
    cur.execute("DELETE FROM review_tags")
    conn.commit()

cur.execute("""
    SELECT r.review_id, r.asin, r.product_name, r.review
    FROM raw_reviews r
    LEFT JOIN review_tags t ON r.review_id = t.review_id
    WHERE t.review_id IS NULL
""")
rows = cur.fetchall()
print(f"Reviews to tag: {len(rows)}")

# ── Prompt ───────────────────────────────────────────────────────────────────────

def build_prompt(batch):
    return f"""You are a strict classification engine for product reviews.

Allowed taxonomy (use ONLY these):
{json.dumps(TAXONOMY, indent=2)}

Rules:
- Classify each review independently using only the categories above.
- Multiple categories are allowed per review.
- Return VALID JSON only — no explanation, no markdown.

Output format:
{{
  "results": [
    {{
      "id": "review_id",
      "sentiment": "Positive | Neutral | Negative",
      "primary_categories": ["..."],
      "sub_tags": ["..."]
    }}
  ]
}}

Reviews:
{json.dumps(batch, indent=2)}
"""

# ── Tag in batches ───────────────────────────────────────────────────────────────

def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]

for batch in chunks(rows, BATCH_SIZE):
    payload = [{"id": row[0], "product": row[2], "text": row[3]} for row in batch]

    try:
        response = client.generate_content(build_prompt(payload))
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        parsed = json.loads(text.strip())
    except Exception as e:
        print(f"Batch failed: {e}")
        continue

    tagged = {item["id"]: item for item in parsed.get("results", [])}

    for review_id, asin, _, _ in batch:
        result = tagged.get(review_id)
        if not result:
            print(f"  Missing result for {review_id}")
            continue
        cur.execute(
            """
            INSERT INTO review_tags (review_id, asin, sentiment, primary_categories, sub_tags)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                review_id,
                asin,
                result.get("sentiment"),
                json.dumps(result.get("primary_categories", [])),
                json.dumps(result.get("sub_tags", [])),
            ),
        )
        conn.commit()

    print(f"  Tagged {len(batch)} reviews")

conn.close()
print("Tagging complete.")
