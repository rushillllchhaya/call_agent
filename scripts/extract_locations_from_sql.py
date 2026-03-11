"""
extract_brand_locations_v3.py

Fixed: handles multi-line INSERT format:
  INSERT INTO `brands` (`id`, `name`, ...) VALUES
  (1,'Formosa',...),
  (2,'Hygain',...);

Joins:
  brands(id, name) + add_new_materials(brand_id, seller_id) + seller_masters(id, gst_city, gst_state)
  -> brand_name -> most common city across its sellers

Unknown brands -> location = "Unknown"
"""
import re
import json
import os
from collections import defaultdict, Counter

SQL_FILE = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "bsbinfotrade_bsb_prime_demo (2) (1).sql"))
OUT_FILE = os.path.join(os.path.dirname(__file__), "brand_locations.json")

TARGET_TABLES = {"brands", "seller_masters", "add_new_materials"}


def split_row(row_str):
    """Split a CSV row respecting single-quoted strings and NULL."""
    parts = re.split(r",(?=(?:[^']*'[^']*')*[^']*$)", row_str)
    result = []
    for p in parts:
        p = p.strip()
        if p.upper() == "NULL":
            result.append(None)
        elif p.startswith("'") and p.endswith("'"):
            result.append(p[1:-1])
        else:
            result.append(p)
    return result


def main():
    print(f"Scanning {SQL_FILE} ...")

    # ── Pass: read all lines into memory per table ─────────────────────────
    col_map = {}      # table -> [col_names from INSERT header]
    data = defaultdict(list)  # table -> list of row dicts

    with open(SQL_FILE, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].rstrip()

        # INSERT INTO `table` (`col1`, `col2`, ...) VALUES
        im = re.match(r"INSERT INTO `([^`]+)` \(([^)]+)\) VALUES", line)
        if im:
            table = im.group(1).lower()
            col_str = im.group(2)
            cols = [c.strip().strip("`").lower() for c in col_str.split(",")]

            if table in TARGET_TABLES:
                col_map[table] = cols
                # Read value rows until semicolon
                i += 1
                while i < n:
                    vline = lines[i].rstrip()
                    # Each value line looks like: (val,val,...),  or (val,...);
                    for row_str in re.findall(r"\(([^)]*(?:'[^']*'[^)]*)*)\)", vline):
                        parts = split_row(row_str)
                        if len(parts) >= len(cols):
                            row = {cols[j]: parts[j] for j in range(len(cols))}
                            data[table].append(row)
                    if vline.rstrip(";").rstrip().endswith(")") and vline.rstrip().endswith(";"):
                        break
                    if ";" in vline:
                        break
                    i += 1
        i += 1

    print(f"brands rows: {len(data['brands'])}")
    print(f"seller_masters rows: {len(data['seller_masters'])}")
    print(f"add_new_materials rows: {len(data['add_new_materials'])}")

    # ── Build lookup dicts ─────────────────────────────────────────────────
    brands = {}   # id -> name
    for row in data["brands"]:
        bid = row.get("id")
        name = row.get("name")
        if bid and name:
            brands[bid] = name

    sellers = {}  # id -> city
    for row in data["seller_masters"]:
        sid = row.get("id")
        city = row.get("gst_city")
        state = row.get("gst_state")
        loc = city if city else state
        if sid and loc:
            sellers[sid] = loc

    brand_sellers = defaultdict(set)  # brand_id -> set of seller_ids
    for row in data["add_new_materials"]:
        bid = row.get("brand_id")
        sid = row.get("seller_id")
        if bid and sid:
            brand_sellers[bid].add(sid)

    print(f"\nBrands index: {len(brands)}, Sellers with city: {len(sellers)}, Brand-seller links: {len(brand_sellers)}")

    # ── Resolve brand -> location ─────────────────────────────────────────
    result = {}
    for brand_id, seller_ids in brand_sellers.items():
        brand_name = brands.get(brand_id)
        if not brand_name:
            continue
        cities = [sellers[s] for s in seller_ids if s in sellers]
        location = Counter(cities).most_common(1)[0][0] if cities else "Unknown"
        result[brand_name.lower()] = location

    # Brands that never appeared in any offer
    for bid, name in brands.items():
        if name.lower() not in result:
            result[name.lower()] = "Unknown"

    resolved = sum(1 for v in result.values() if v != "Unknown")
    print(f"\nTotal brands: {len(result)} | Resolved: {resolved} | Unknown: {len(result)-resolved}")

    print("\n== Sample ==")
    for k, v in list(result.items())[:25]:
        print(f"  {k!r:40s} -> {v!r}")

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to {OUT_FILE}")


if __name__ == "__main__":
    main()
