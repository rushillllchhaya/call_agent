import re

sql_file = r"c:\Users\ADMIN\Desktop\Project\bsbinfotrade_bsb_prime_demo (2) (1).sql"
output_file = r"c:\Users\ADMIN\Desktop\Project\brands_list.txt"

def extract_table(sql_text, table_name):
    pattern = re.compile(
        r"INSERT INTO [`]?" + re.escape(table_name) + r"[`]?\s+\([^)]+\)\s+VALUES\s+(.*?);",
        re.IGNORECASE | re.DOTALL
    )
    matches = pattern.findall(sql_text)
    rows = []
    for values_block in matches:
        depth = 0
        current = []
        in_quote = False
        quote_char = None
        token = ""
        for ch in values_block:
            if in_quote:
                if ch == quote_char:
                    in_quote = False
                token += ch
            elif ch in ("'", '"'):
                in_quote = True
                quote_char = ch
                token += ch
            elif ch == '(':
                depth += 1
                if depth == 1:
                    token = ""
                else:
                    token += ch
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    current.append(token.strip())
                    rows.append(current)
                    current = []
                else:
                    token += ch
            elif ch == ',' and depth == 1:
                current.append(token.strip())
                token = ""
            else:
                token += ch
    return rows

def get_column_names(sql_text, table_name):
    pattern = re.compile(
        r"INSERT INTO [`]?" + re.escape(table_name) + r"[`]?\s+\(([^)]+)\)\s+VALUES",
        re.IGNORECASE
    )
    m = pattern.search(sql_text)
    if m:
        return [c.strip().strip('`') for c in m.group(1).split(',')]
    return []

def clean(val):
    if val is None:
        return None
    val = val.strip()
    if val.startswith("'") and val.endswith("'"):
        val = val[1:-1]
    if val.upper() == "NULL":
        return None
    return val

def to_dict(cols, row):
    return {cols[i]: clean(row[i]) for i in range(min(len(cols), len(row)))}

print("Reading SQL file...")
with open(sql_file, "r", encoding="utf-8", errors="ignore") as f:
    sql = f.read()

# --- BRANDS: id -> (name) ---
brand_cols = get_column_names(sql, "brands")
brands = {}
for r in extract_table(sql, "brands"):
    d = to_dict(brand_cols, r)
    if d.get('id', '').isdigit():
        brands[d['id']] = d.get('name', '')

# --- GRADES: id -> name ---
grade_cols = get_column_names(sql, "grades")
grades = {}
for r in extract_table(sql, "grades"):
    d = to_dict(grade_cols, r)
    if d.get('id', '').isdigit():
        grades[d['id']] = d.get('name', '')

# --- CODES: id -> {name, grade_id, brand_id} ---
code_cols = get_column_names(sql, "codes")
codes = {}
for r in extract_table(sql, "codes"):
    d = to_dict(code_cols, r)
    if d.get('id', '').isdigit():
        codes[d['id']] = d

# --- ADD_NEW_MATERIALS: grade_code_id -> price ---
mat_cols = get_column_names(sql, "add_new_materials")
prices = {}  # code_id -> list of prices
for r in extract_table(sql, "add_new_materials"):
    d = to_dict(mat_cols, r)
    cid = d.get('grade_code_id', '')
    price = d.get('original_price', '')
    if cid and price:
        if cid not in prices:
            prices[cid] = []
        prices[cid].append(price)

# --- BUILD FINAL LIST ---
results = []
for cid, c in codes.items():
    brand_id = c.get('brand_id', '')
    grade_id = c.get('grade_id', '')
    code_name = c.get('name', '')
    brand_name = brands.get(brand_id, 'Unknown')
    material = grades.get(grade_id, 'Unknown')
    # Get price (take first/lowest if multiple)
    price_list = prices.get(cid, [])
    if price_list:
        price = min(price_list, key=lambda p: float(p) if p else 0)
        price_str = f"Rs. {float(price):.2f}"
    else:
        price_str = "N/A"
    results.append((material, brand_name, code_name, price_str))

# Sort by material, brand, code
results.sort(key=lambda x: (x[0], x[1], x[2]))

# --- WRITE TO FILE ---
header = f"{'#':<5} {'Material':<25} {'Brand Name':<35} {'Code':<20} {'Price'}"
separator = "-" * 100

lines = []
lines.append("BSB BRAND LIST - Material, Brand Name, Code & Price")
lines.append("=" * 100)
lines.append(f"Generated from database | Total: {len(results)} entries")
lines.append("=" * 100)
lines.append("")
lines.append(header)
lines.append(separator)

current_material = None
for i, (material, brand_name, code_name, price_str) in enumerate(results, 1):
    if material != current_material:
        if current_material is not None:
            lines.append("")
        lines.append(f"  >>> {material} <<<")
        lines.append(separator)
        current_material = material
    lines.append(f"{i:<5} {material:<25} {brand_name:<35} {code_name:<20} {price_str}")

lines.append("")
lines.append("=" * 100)
lines.append(f"Total entries: {len(results)}")

with open(output_file, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print(f"Saved to: {output_file}")
print(f"Total entries: {len(results)}")

# Preview first 10
print("\nPreview (first 10 lines):")
print(header)
print(separator)
for i, (material, brand_name, code_name, price_str) in enumerate(results[:10], 1):
    print(f"{i:<5} {brand_name:<35} {material:<25} {code_name:<20} {price_str}")
