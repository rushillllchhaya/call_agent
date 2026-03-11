"""
sync_locations_from_brands_list.py

Reads brands_list.txt, extracts any manually-added locations that are not 'Unknown',
then syncs them back to prices_cache.json by matching on brand + grade.

brands_list.txt column layout (fixed-width):
  Material(0-24)  Brand(25-59)  Code(60-81)  Price(82-93)  Location(94+)

Run: python scripts/sync_locations_from_brands_list.py
"""
import json
import os

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
BRANDS_FILE = os.path.join(ROOT, "brands_list.txt")
CACHE_FILE  = os.path.join(ROOT, "prices_cache.json")


def parse_brands_list():
    """
    Returns list of (material, brand, code, price_str, location)
    only for rows that have a real location (not Unknown/empty).
    """
    entries = []
    with open(BRANDS_FILE, encoding="utf-8") as f:
        for line in f:
            # Skip headers / separators / section titles
            stripped = line.rstrip()
            if not stripped or stripped.startswith("#") or stripped.startswith("=") \
               or stripped.startswith("-") or stripped.startswith("B") and "BSB" in stripped \
               or ">>>" in stripped or "Generated" in stripped or "Total" in stripped \
               or stripped.startswith("Material"):
                continue

            # Fixed-width parse - columns are space-separated with at least 2 spaces
            # Split into tokens to find location (last field)
            # Format: MAT   BRAND   CODE   PRICE   LOCATION
            # Use split with maxsplit to preserve location (may have spaces)
            import re
            # Match: material  brand  code  price  location
            # All separated by 2+ spaces
            parts = re.split(r"  +", stripped.strip())
            if len(parts) >= 5:
                material  = parts[0].strip()
                brand     = parts[1].strip()
                code      = parts[2].strip()
                price_str = parts[3].strip()
                location  = " ".join(parts[4:]).strip()

                if location and location.lower() not in ("unknown", "location", ""):
                    entries.append((material, brand, code, price_str, location))

    return entries


def main():
    entries = parse_brands_list()
    print(f"Found {len(entries)} manually-set locations in brands_list.txt:")
    for m, b, c, p, l in entries:
        print(f"  {b:30s} {c:20s} -> {l}")

    if not entries:
        print("Nothing to sync.")
        return

    # Load cache
    with open(CACHE_FILE, encoding="utf-8") as f:
        cache = json.load(f)

    # Build index: (brand.lower, grade.lower) -> cache item
    cache_index = {}
    for item in cache:
        key = (item.get("brand","").lower().strip(), item.get("grade","").lower().strip())
        cache_index[key] = item

    updated = 0
    not_found = []
    for material, brand, code, price_str, location in entries:
        key = (brand.lower().strip(), code.lower().strip())
        if key in cache_index:
            old = cache_index[key].get("location", "")
            cache_index[key]["location"] = location
            updated += 1
            if old != location:
                print(f"  [updated] {brand} / {code}: '{old}' -> '{location}'")
        else:
            not_found.append((brand, code))

    if not_found:
        print(f"\nNot found in cache ({len(not_found)}):")
        for b, c in not_found:
            print(f"  {b} / {c}")

    # Write back
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)

    print(f"\nSynced {updated} locations to prices_cache.json")


if __name__ == "__main__":
    main()
