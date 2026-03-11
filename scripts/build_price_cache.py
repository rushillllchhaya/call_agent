"""
Build prices_cache.json from the SQL dump file.
Extracts brand, grade code, category (material), and price — no DB writes.
Output: prices_cache.json in project root.
"""
import re
import json
import os

SQL_FILE = r"c:\Users\ADMIN\Desktop\Project\bsbinfotrade_bsb_prime_demo (2) (1).sql"
OUT_FILE = r"c:\Users\ADMIN\Desktop\Project\prices_cache.json"

def parse_inserts(sql_text, table_name):
    pattern = re.compile(
        rf"INSERT INTO `{table_name}`[^;]+?VALUES\s*(.*?);",
        re.DOTALL | re.IGNORECASE
    )
    row_pattern = re.compile(r"\(([^()]*(?:\([^()]*\)[^()]*)*)\)")
    results = []
    for match in pattern.finditer(sql_text):
        values_block = match.group(1)
        for row_match in row_pattern.finditer(values_block):
            raw = row_match.group(1)
            fields = []
            current = ""
            in_quote = False
            for ch in raw:
                if ch == "'" and not in_quote:
                    in_quote = True
                elif ch == "'" and in_quote:
                    in_quote = False
                elif ch == "," and not in_quote:
                    fields.append(current.strip())
                    current = ""
                    continue
                else:
                    current += ch
            fields.append(current.strip())
            cleaned = [f.strip("'") if f != "NULL" else None for f in fields]
            results.append(cleaned)
    return results

print("Reading SQL file...")
with open(SQL_FILE, "r", encoding="utf-8", errors="ignore") as f:
    sql = f.read()

# Parse tables
brands_rows = parse_inserts(sql, "brands")
grades_rows  = parse_inserts(sql, "grades")
codes_rows   = parse_inserts(sql, "codes")
offers_rows  = parse_inserts(sql, "add_new_materials")

# Build lookup dicts
brands = {}   # id -> name
for r in brands_rows:
    if len(r) >= 2:
        brands[r[0]] = r[1]  # id -> name

grades = {}   # id -> name (PVC, HDPE etc.)
for r in grades_rows:
    if len(r) >= 2:
        grades[r[0]] = r[1]

# codes: id, name(grade_code), grade_id, brand_id  (column order from SQL dump)
# Determine column order from dump header
# Schema: id, name, grade_id, status, order_no, brand_id, mfi_value, ...
# Let's print first few to check
if codes_rows:
    print("Sample code row:", codes_rows[0])

codes = {}   # id -> {name, grade_id, brand_id}
for r in codes_rows:
    if len(r) >= 6:
        # Actual schema: id=0, name=1, grade_id=2(string e.g.'K58'), status=3,
        #                order_no=4, brand_id=5
        try:
            codes[r[0]] = {
                "name":     r[1],   # grade code string e.g. 'TL700'
                "grade_id": r[3],   # col index 3 = grade_id numeric FK (e.g '1')
                "brand_id": r[5],   # col index 5 = brand_id numeric FK
            }
        except IndexError:
            pass

# Schema: id(0), created_by(1), updated_by(2), seller_id(3), polymer_id(4), grade_code_id(5),
#         grade_id(6), process_type_id(7), brand_id(8), packaging_bag_id(9),
#         instock_in_kg(10), instock(11), moq_in_kg(12), moq(13), original_price(14)
prices = {}  # grade_code_id -> original_price
for r in offers_rows:
    if len(r) >= 15:
        gc_id = r[5]   # grade_code_id
        price = r[14]  # original_price
        if gc_id and price and price not in (None, "0.00", "0", ""):
            try:
                p = float(price)
                if p > 0 and gc_id not in prices:
                    prices[gc_id] = p
            except ValueError:
                pass

# Build final cache
cache = []
for code_id, code_data in codes.items():
    brand_id  = code_data["brand_id"]
    grade_id  = code_data["grade_id"]
    brand_name = brands.get(brand_id, "")
    category   = grades.get(grade_id, "")
    price_per_kg = prices.get(code_id)

    cache.append({
        "code_id":     code_id,
        "brand":       brand_name,
        "grade":       code_data["name"],
        "category":    category,
        "price_per_kg": price_per_kg,
        "price_per_mt": round(price_per_kg * 1000, 2) if price_per_kg else None,
    })

cache.sort(key=lambda x: (x["category"], x["brand"], x["grade"]))

with open(OUT_FILE, "w", encoding="utf-8") as f:
    json.dump(cache, f, indent=2, ensure_ascii=False)

priced = sum(1 for c in cache if c["price_per_kg"])
print(f"Done! {len(cache)} entries written ({priced} with prices) -> {OUT_FILE}")
if cache:
    print("Sample:", json.dumps(cache[0], indent=2))
