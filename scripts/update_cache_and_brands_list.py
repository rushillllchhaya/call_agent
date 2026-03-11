"""
update_cache_and_brands_list.py

1) Updates prices_cache.json with real locations from SQL extraction.
2) Regenerates brands_list.txt with a Location column added.

Real SQL-sourced locations (from brands -> add_new_materials -> seller_masters):
  Hygain   -> Solan
  Hanwha   -> Solan
  Formosa  -> Ahmedabad

All others -> Unknown (genuinely not in DB)

Run: python scripts/update_cache_and_brands_list.py
"""
import json
import os

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
CACHE_PATH = os.path.join(ROOT, "prices_cache.json")
BRANDS_PATH = os.path.join(ROOT, "brands_list.txt")
BRAND_LOC_PATH = os.path.join(os.path.dirname(__file__), "brand_locations.json")

# ── Real locations from SQL (brand name lower -> city) ─────────────────────
SQL_LOCATIONS: dict[str, str] = {
    "hygain":  "Solan",
    "hanwha":  "Solan",
    "formosa": "Ahmedabad",
}


def get_location(brand: str) -> str:
    return SQL_LOCATIONS.get(brand.strip().lower(), "Unknown")


# ── 1. Update prices_cache.json ────────────────────────────────────────────
def update_cache():
    with open(CACHE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    changed = 0
    for item in data:
        new_loc = get_location(item.get("brand", ""))
        if item.get("location") != new_loc:
            item["location"] = new_loc
            changed += 1

    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"[cache] Updated {changed} entries. Total: {len(data)}")


# ── 2. Rebuild brands_list.txt with Location column ───────────────────────
HEADER = (
    "BSB BRAND LIST - Material, Brand Name, Code, Price & Location\n"
    "=====================================================================================================================================\n"
    "Generated from database | Total: 422 entries\n"
    "=====================================================================================================================================\n"
    "\n"
    "#     {:<25s} {:<35s} {:<22s} {:<12s} {}\n".format(
        "Material", "Brand Name", "Code", "Price", "Location"
    ) +
    "-------------------------------------------------------------------------------------------------------------------------------------\n"
)

SECTION_SEP = "-------------------------------------------------------------------------------------------------------------------------------------"


def format_row(material, brand, code, price, location):
    return "{:<25s} {:<35s} {:<22s} {:<12s} {}".format(
        material, brand, code, price, location
    )


def rebuild_brands_list():
    with open(CACHE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Group by category (material)
    from collections import defaultdict
    by_cat = defaultdict(list)
    for item in data:
        cat = item.get("category") or "Unknown"
        by_cat[cat].append(item)

    cat_order = ["HDPE", "LDPE", "LLDPE", "PET", "PP", "PVC"]
    # any extra categories not in our list
    extra = sorted(set(by_cat.keys()) - set(cat_order))

    lines = [HEADER]
    total = 0

    for cat in cat_order + extra:
        items = by_cat.get(cat)
        if not items:
            continue
        lines.append(f"\n  >>> {cat} <<<\n{SECTION_SEP}\n")
        for item in items:
            brand = item.get("brand", "")
            grade = item.get("grade", "")
            pkg = item.get("price_per_kg")
            price_str = f"Rs. {pkg:.2f}" if pkg else "N/A"
            location = item.get("location") or "Unknown"
            lines.append(format_row(cat, brand, grade, price_str, location))
            total += 1

    lines.append(f"\n{'='*69}\nTotal entries: {total}\n")

    with open(BRANDS_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[brands_list] Written {total} entries to {BRANDS_PATH}")


if __name__ == "__main__":
    update_cache()
    rebuild_brands_list()
    print("Done.")
