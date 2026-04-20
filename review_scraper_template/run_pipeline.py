"""
run_pipeline.py
Runs scraper → tagger in sequence.
Usage: python run_pipeline.py
"""

import subprocess
import sys
import os

DIR = os.path.dirname(os.path.abspath(__file__))

steps = [
    ("Scraping reviews",  os.path.join(DIR, "scraper_runner.py")),
    ("Tagging reviews",   os.path.join(DIR, "tagger.py")),
]

for i, (label, script) in enumerate(steps, 1):
    print(f"\nStep {i}/{len(steps)}: {label}...")
    try:
        subprocess.check_call([sys.executable, script], cwd=DIR)
    except subprocess.CalledProcessError as e:
        print(f"Pipeline failed at step {i} ({label}): {e}")
        sys.exit(1)

print("\nPipeline complete.")
