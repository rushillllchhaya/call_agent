from fpdf import FPDF

input_file = r"c:\Users\ADMIN\Desktop\Project\brands_list.txt"
output_file = r"c:\Users\ADMIN\Desktop\Project\brands_list.pdf"

class PDF(FPDF):
    def header(self):
        self.set_font("Courier", "B", 10)
        self.set_fill_color(6, 78, 141)       # BSB dark blue
        self.set_text_color(255, 255, 255)
        self.cell(0, 8, "BSB INFOTRADE - Brand & Material Code List", align="C", fill=True, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(2)

    def footer(self):
        self.set_y(-12)
        self.set_font("Courier", "I", 7)
        self.set_text_color(128, 128, 128)
        self.cell(0, 5, f"Page {self.page_no()} | Generated from BSB Database", align="C")

pdf = PDF(orientation="L", unit="mm", format="A4")   # Landscape for wide columns
pdf.set_auto_page_break(auto=True, margin=15)
pdf.add_page()
pdf.set_margins(10, 15, 10)

# Column widths (landscape A4 = 297mm wide, minus margins)
# Material | Brand Name | Code | Price
col_widths = [28, 70, 50, 28]
col_headers = ["Material", "Brand Name", "Code", "Price"]

def draw_table_header(pdf):
    pdf.set_font("Courier", "B", 8)
    pdf.set_fill_color(230, 240, 255)
    pdf.set_text_color(6, 78, 141)
    for i, (header, w) in enumerate(zip(col_headers, col_widths)):
        pdf.cell(w, 7, header, border=1, fill=True)
    pdf.ln()
    pdf.set_text_color(0, 0, 0)

with open(input_file, "r", encoding="utf-8") as f:
    lines = f.readlines()

current_material = None
row_count = 0

draw_table_header(pdf)

for line in lines:
    line = line.rstrip("\r\n")

    # Section header (>>> MATERIAL <<<)
    if line.strip().startswith(">>>") and "<<<" in line:
        material_name = line.strip().strip(">").strip("<").strip()
        # Section divider row
        pdf.set_font("Courier", "B", 8)
        pdf.set_fill_color(6, 78, 141)
        pdf.set_text_color(255, 255, 255)
        total_w = sum(col_widths)
        pdf.cell(total_w, 6, f"  {material_name}", border=0, fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        current_material = material_name
        row_count = 0
        draw_table_header(pdf)
        continue

    # Skip separator lines and blank metadata lines
    if (line.startswith("=") or line.startswith("-") or
        line.startswith("BSB BRAND") or line.startswith("Generated") or
        line.startswith("Total entries") or line.strip() == "" or
        line.strip().startswith("#")):
        continue

    # Parse data row: "Material   Brand Name   Code   Price"
    parts = line.split()
    if len(parts) < 3:
        continue

    # Re-parse by fixed column positions since we know the format
    # Material is first word (no spaces), Brand Name may have spaces, Code follows, Price is last
    # Use the known column structure from file: Material(25) BrandName(35) Code(20) Price
    stripped = line
    material = stripped[:25].strip()
    brand    = stripped[25:60].strip()
    code     = stripped[60:80].strip()
    price    = stripped[80:].strip()

    if not material or not brand:
        continue

    # Alternate row shading
    if row_count % 2 == 0:
        pdf.set_fill_color(245, 249, 255)
    else:
        pdf.set_fill_color(255, 255, 255)

    pdf.set_font("Courier", "", 7.5)
    pdf.cell(col_widths[0], 6, material, border=1, fill=True)
    pdf.cell(col_widths[1], 6, brand,    border=1, fill=True)
    pdf.cell(col_widths[2], 6, code,     border=1, fill=True)

    # Price coloring
    if price and price != "N/A":
        pdf.set_text_color(0, 130, 60)   # green for available prices
        pdf.set_font("Courier", "B", 7.5)
    else:
        pdf.set_text_color(160, 160, 160)
        pdf.set_font("Courier", "", 7.5)

    pdf.cell(col_widths[3], 6, price or "N/A", border=1, fill=True)
    pdf.set_text_color(0, 0, 0)
    pdf.ln()
    row_count += 1

pdf.output(output_file)
print(f"PDF saved to: {output_file}")
print(f"Pages: {pdf.page}")
