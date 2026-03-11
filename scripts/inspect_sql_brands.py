"""
inspect_sql_brands.py — inspect brand/seller tables in the SQL dump
"""
import re
import os

SQL_FILE = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "bsbinfotrade_bsb_prime_demo (2) (1).sql"))

TARGET_TABLES = {"brands", "seller_masters", "add_new_materials", "product_offers"}

def main():
    col_map = {}
    current_table = None
    current_cols = []
    in_create = False
    sample_inserts = {}   # table -> first raw INSERT line

    with open(SQL_FILE, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip()

            # Detect CREATE TABLE
            m = re.match(r"CREATE TABLE `([^`]+)`", line)
            if m:
                current_table = m.group(1).lower()
                current_cols = []
                in_create = True
                continue

            if in_create:
                if line.strip().startswith(")"):
                    if current_table and current_table in TARGET_TABLES:
                        col_map[current_table] = current_cols
                    in_create = False
                    current_table = None
                else:
                    cm = re.match(r"\s+`([^`]+)`", line)
                    if cm:
                        current_cols.append(cm.group(1).lower())

            # Grab first INSERT for each target table
            im = re.match(r"INSERT INTO `([^`]+)` VALUES (.+);", line)
            if im:
                tbl = im.group(1).lower()
                if tbl in TARGET_TABLES and tbl not in sample_inserts:
                    sample_inserts[tbl] = im.group(2)[:300]

    print("\n=== COLUMN SCHEMAS ===")
    for t, cols in col_map.items():
        print(f"\n[{t}]\n  Columns: {cols}")

    print("\n=== SAMPLE INSERT DATA (first 300 chars) ===")
    for t, data in sample_inserts.items():
        print(f"\n[{t}]\n  {data}")

if __name__ == "__main__":
    main()
