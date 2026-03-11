import re

sql_file = r"c:\Users\ADMIN\Desktop\Project\bsbinfotrade_bsb_prime_demo (2) (1).sql"

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

print("Reading SQL file...")
with open(sql_file, "r", encoding="utf-8", errors="ignore") as f:
    sql = f.read()

# --- BRANDS ---
brand_cols = get_column_names(sql, "brands")
brand_rows = extract_table(sql, "brands")
brands = {}
for r in brand_rows:
    d = {brand_cols[i]: clean(r[i]) for i in range(min(len(brand_cols), len(r)))}
    if d.get('id', '').isdigit():
        brands[d['id']] = (d.get('name', ''), d.get('code', ''))

# --- GRADES ---
grade_cols = get_column_names(sql, "grades")
grade_rows = extract_table(sql, "grades")
grades = {}
for r in grade_rows:
    d = {grade_cols[i]: clean(r[i]) for i in range(min(len(grade_cols), len(r)))}
    if d.get('id', '').isdigit():
        grades[d['id']] = (d.get('name', ''), d.get('code', ''))

# --- CODES ---
code_cols = get_column_names(sql, "codes")
code_rows = extract_table(sql, "codes")

print(f"\n{'#':<5} {'Brand Name':<35} {'Brand Code':<15} {'Grade (Category)':<25} {'Code Name'}")
print("=" * 100)

results = []
for r in code_rows:
    d = {code_cols[i]: clean(r[i]) for i in range(min(len(code_cols), len(r)))}
    brand_id = d.get('brand_id', '')
    grade_id = d.get('grade_id', '')
    code_name = d.get('name', '')

    bname, bcode = brands.get(brand_id, ('Unknown', ''))
    gname, gcode = grades.get(grade_id, ('Unknown', ''))

    results.append((bname, bcode, gname, code_name))

# Sort by grade category, then brand name, then code
results.sort(key=lambda x: (x[2], x[0], x[3]))

for i, (bname, bcode, gname, code_name) in enumerate(results, 1):
    print(f"{i:<5} {bname:<35} {bcode:<15} {gname:<25} {code_name}")

print(f"\nTotal: {len(results)} entries")
