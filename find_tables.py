import re

tables = {}
curr = None
with open(r'c:\Users\ADMIN\Desktop\Project\bsbinfotrade_bsb_prime_demo (2) (1).sql', 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line.startswith('CREATE TABLE `'):
            curr = line.split('`')[1]
            tables[curr] = []
        elif curr and line.startswith('`'):
            col = line.split('`')[1]
            tables[curr].append(col)
        elif curr and line.startswith(') ENGINE'):
            curr = None

for t, cols in tables.items():
    if 'name' in cols and ('grade_id' in cols or 'brand_id' in cols):
        print(t, cols)
