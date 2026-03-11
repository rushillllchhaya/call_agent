"""
pdf_generator.py

Reads prices_cache.json (or brands_list.txt) and generates a beautifully formatted 
PDF catalog of all BSB materials with their Prices and Locations.
"""
import json
import os
from collections import defaultdict
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
CACHE_FILE = os.path.join(ROOT, "prices_cache.json")
OUT_FILE = os.path.join(ROOT, "BSB_Material_Prices.pdf")

def generate_pdf():
    # Load data
    with open(CACHE_FILE, encoding="utf-8") as f:
        data = json.load(f)

    # Group by category (Material)
    by_cat = defaultdict(list)
    for item in data:
        cat = item.get("category", "Unknown").upper()
        by_cat[cat].append(item)

    # Standard order
    cat_order = ["HDPE", "LDPE", "LLDPE", "PET", "PP", "PVC"]
    extra_cats = sorted(set(by_cat.keys()) - set(cat_order))
    all_cats = cat_order + extra_cats

    # Doc setup
    doc = SimpleDocTemplate(
        OUT_FILE, 
        pagesize=A4, # Portrait is fine if columns fit
        rightMargin=40, leftMargin=40, 
        topMargin=40, bottomMargin=40
    )
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'TitleStyle', parent=styles['Heading1'],
        fontSize=24, textColor=colors.HexColor("#1e3a8a"),
        spaceAfter=20, alignment=1 # Center
    )
    subtitle_style = ParagraphStyle(
        'SubtitleStyle', parent=styles['Normal'],
        fontSize=12, textColor=colors.HexColor("#64748b"),
        spaceAfter=30, alignment=1
    )
    category_style = ParagraphStyle(
        'CategoryStyle', parent=styles['Heading2'],
        fontSize=16, textColor=colors.HexColor("#0f172a"),
        spaceBefore=20, spaceAfter=10
    )

    elements = []

    # Header
    elements.append(Paragraph("BSB Material Price Catalog", title_style))
    elements.append(Paragraph(f"Generated directly from database | Total Items: {len(data)}", subtitle_style))

    # Tables per category
    for cat in all_cats:
        items = by_cat.get(cat)
        if not items:
            continue
            
        # Category Title
        elements.append(Paragraph(f"{cat} Pricing", category_style))

        # Build table data
        table_data = [["Brand Name", "Grade Code", "Price/kg (Rs.)", "Location"]]
        
        # Sort items within category by Brand, then Grade
        items.sort(key=lambda x: (x.get("brand", "").lower(), x.get("grade", "").lower()))
        
        for item in items:
            brand = item.get("brand", "")
            grade = item.get("grade", "")
            pkg = item.get("price_per_kg")
            price_str = f"{pkg:.2f}" if pkg else "N/A"
            loc = item.get("location", "Unknown")
            
            table_data.append([brand, grade, price_str, loc])

        # Table formatting
        # A4 width = ~595 points. usable = 595 - 80 = 515.
        col_widths = [140, 110, 80, 185]
        
        t = Table(table_data, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1e3a8a")),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (2, 0), (2, -1), 'CENTER'), # Price centered
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('TOPPADDING', (0, 0), (-1, 0), 10),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor("#f8fafc")),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('PADDING', (0, 1), (-1, -1), 6),
        ]))
        
        # Add alternating row colors
        for i in range(1, len(table_data)):
            if i % 2 == 0:
                t.setStyle(TableStyle([('BACKGROUND', (0, i), (-1, i), colors.HexColor("#f1f5f9"))]))
                
        elements.append(t)
        elements.append(Spacer(1, 15))

    # Build PDF
    doc.build(elements)
    print(f"Successfully generated {OUT_FILE}")

if __name__ == "__main__":
    generate_pdf()
