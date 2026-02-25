import sqlite3
import re

SQL_FILE = r'c:\Users\ADMIN\Desktop\Project\bsbinfotrade_bsb_prime_demo (2) (1).sql'
DB_FILE = r'c:\Users\ADMIN\Desktop\Project\conversations.db'

# We map Source Table (in MariaDB) -> Destination Table (in SQLite)
TARGET_TABLES = {
    'grades': 'grades',
    'brands': 'brands',
    'codes': 'brand_grade_maps',  # The missing link
    'add_new_materials': 'add_new_materials',
    'polymer_inventory': 'polymer_inventory',
    'pricing_rules': 'pricing_rules'
}

def clean_statement(stmt):
    stmt = stmt.replace("\\'", "''")
    return stmt

conn = sqlite3.connect(DB_FILE)
cursor = conn.cursor()

# Drop existing
for dest_t in TARGET_TABLES.values():
    cursor.execute(f"DROP TABLE IF EXISTS `{dest_t}`")
conn.commit()

created_tables = set()

with open(SQL_FILE, 'r', encoding='utf-8') as f:
    stmt_buffer = []
    in_target_insert = False
    source_table_name = ""
    dest_table_name = ""
    
    for line in f:
        if line.startswith('INSERT INTO `'):
            m = re.match(r'INSERT INTO `([^`]+)` \(([^)]+)\)', line)
            if m:
                t_name = m.group(1)
                col_str = m.group(2)
                
                if t_name in TARGET_TABLES:
                    d_name = TARGET_TABLES[t_name]
                    if d_name not in created_tables:
                        cols = [c.strip().strip('`') for c in col_str.split(',')]
                        col_defs = []
                        for c in cols:
                            if c == 'id':
                                col_defs.append("`id` INTEGER PRIMARY KEY")
                            else:
                                col_defs.append(f"`{c}` TEXT")
                        
                        create_stmt = f"CREATE TABLE `{d_name}` ({', '.join(col_defs)});"
                        print(f"Creating {d_name} from source {t_name} with columns: {cols}")
                        cursor.execute(create_stmt)
                        created_tables.add(d_name)
                    
                    in_target_insert = True
                    source_table_name = t_name
                    dest_table_name = d_name
                    # Rename the target in the insert statement
                    modified_line = line.replace(f"`{t_name}`", f"`{d_name}`", 1)
                    stmt_buffer.append(modified_line)
        elif in_target_insert:
            stmt_buffer.append(line)
            if line.rstrip().endswith(';'):
                full_stmt = "".join(stmt_buffer)
                full_stmt = clean_statement(full_stmt)
                
                try:
                    cursor.execute(full_stmt)
                except Exception as e:
                    print(f"Error inserting into {dest_table_name}: {e}")
                
                stmt_buffer = []
                in_target_insert = False
                source_table_name = ""
                dest_table_name = ""

conn.commit()
conn.close()
print("Migration script completed successfully.")
