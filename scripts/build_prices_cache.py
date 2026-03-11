"""
Parse brands_list.txt and build prices_cache.json.
Includes ALL rows — priced ones have price_per_kg set, N/A ones have it null.
"""

import json
import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRANDS_TXT = os.path.join(BASE_DIR, "brands_list.txt")
CACHE_JSON = os.path.join(BASE_DIR, "prices_cache.json")

entries = []

with open(BRANDS_TXT, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        # Skip headers / separators / blank lines
        if not line or line.startswith("#") or line.startswith("=") or line.startswith("-") or line.startswith(">") or line.startswith("BSB") or line.startswith("Generated") or line.startswith("Total"):
            continue

        # Format:  MATERIAL   BRAND   CODE   PRICE
        # Columns are space-separated with wide padding — split on 2+ spaces
        parts = re.split(r"\s{2,}", line)
        if len(parts) < 4:
            continue

        material = parts[0].strip()
        brand    = parts[1].strip()
        grade    = parts[2].strip()
        price_str = parts[3].strip()

        # Parse price
        price_per_kg = None
        price_per_mt = None
        if price_str.lower() != "n/a":
            m = re.search(r"\d[\d.]*", price_str)
            if m:
                price_per_kg = float(m.group().replace(",", ""))
                price_per_mt = round(price_per_kg * 1000)

        entries.append({
            "category": material,
            "brand": brand,
            "grade": grade,
            "price_per_kg": price_per_kg,
            "price_per_mt": price_per_mt,
        })

with open(CACHE_JSON, "w", encoding="utf-8") as f:
    json.dump(entries, f, indent=2, ensure_ascii=False)

priced   = [e for e in entries if e["price_per_kg"] is not None]
unpriced = [e for e in entries if e["price_per_kg"] is None]

print(f"[OK] Written {len(entries)} entries to {CACHE_JSON}")
print(f"   With price    : {len(priced)}")
print(f"   N/A (no price): {len(unpriced)}")

# Show Hygain specifically
hygain = [e for e in entries if "hygain" in e["brand"].lower()]
if hygain:
    print("\nHygain entries:")
    for e in hygain:
        print(f"  {e}")
