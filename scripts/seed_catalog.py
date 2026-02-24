"""
seed_catalog.py — Seeds the polymer_inventory and grades tables with realistic test data.
Run once: python3 scripts/seed_catalog.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.core.models import PolymerType, PolymerInventory, PricingRule

db = SessionLocal()

try:
    # --- 1. Polymer Types (grades table) ---
    polymers_to_add = [
        {"name": "PVC",   "code": "PVC"},
        {"name": "HDPE",  "code": "HDPE"},
        {"name": "LDPE",  "code": "LDPE"},
        {"name": "LLDPE", "code": "LLDPE"},
        {"name": "PP",    "code": "PP"},
        {"name": "PET",   "code": "PET"},
    ]
    for p in polymers_to_add:
        existing = db.query(PolymerType).filter(PolymerType.name == p["name"]).first()
        if not existing:
            db.add(PolymerType(name=p["name"], code=p["code"], status='1'))
            print(f"  Added polymer type: {p['name']}")
        else:
            print(f"  Already exists: {p['name']}")

    db.commit()

    # --- 2. Polymer Inventory (grades + prices) ---
    inventory_to_add = [
        # PVC grades
        {"polymer_type": "PVC",   "grade": "K-67",      "base_price": 60.25, "available_quantity": 500.0,  "warehouse_location": "Mundra"},
        {"polymer_type": "PVC",   "grade": "K-70",      "base_price": 62.00, "available_quantity": 200.0,  "warehouse_location": "Mundra"},
        {"polymer_type": "PVC",   "grade": "SG-5",      "base_price": 61.50, "available_quantity": 150.0,  "warehouse_location": "Ahmedabad"},
        # HDPE grades
        {"polymer_type": "HDPE",  "grade": "H030 SG",   "base_price": 88.00, "available_quantity": 100.0,  "warehouse_location": "Ahmedabad"},
        {"polymer_type": "HDPE",  "grade": "2426H",     "base_price": 87.50, "available_quantity": 250.0,  "warehouse_location": "Mundra"},
        {"polymer_type": "HDPE",  "grade": "220H",      "base_price": 86.00, "available_quantity": 300.0,  "warehouse_location": "Mundra"},
        # LDPE grades
        {"polymer_type": "LDPE",  "grade": "LD-150",    "base_price": 88.00, "available_quantity": 80.0,   "warehouse_location": "Bhiwandi"},
        {"polymer_type": "LDPE",  "grade": "LD-170",    "base_price": 89.50, "available_quantity": 60.0,   "warehouse_location": "Bhiwandi"},
        # LLDPE grades
        {"polymer_type": "LLDPE", "grade": "LL-1002",   "base_price": 85.00, "available_quantity": 120.0,  "warehouse_location": "Mundra"},
        {"polymer_type": "LLDPE", "grade": "LL-1004",   "base_price": 86.00, "available_quantity": 90.0,   "warehouse_location": "Mundra"},
        # PP grades
        {"polymer_type": "PP",    "grade": "H030 SG",   "base_price": 90.00, "available_quantity": 100.0,  "warehouse_location": "Ahmedabad"},
        {"polymer_type": "PP",    "grade": "1030 EG",   "base_price": 82.00, "available_quantity": 200.0,  "warehouse_location": "Mundra"},
        {"polymer_type": "PP",    "grade": "HP-500N",   "base_price": 83.50, "available_quantity": 150.0,  "warehouse_location": "Mundra"},
        # PET grades
        {"polymer_type": "PET",   "grade": "IV-0.80",   "base_price": 82.50, "available_quantity": 75.0,   "warehouse_location": "Mundra"},
        {"polymer_type": "PET",   "grade": "IV-0.84",   "base_price": 84.00, "available_quantity": 50.0,   "warehouse_location": "Mundra"},
    ]

    for item in inventory_to_add:
        existing = db.query(PolymerInventory).filter(
            PolymerInventory.polymer_type == item["polymer_type"],
            PolymerInventory.grade == item["grade"]
        ).first()
        if not existing:
            db.add(PolymerInventory(**item))
            print(f"  Added inventory: {item['polymer_type']} {item['grade']} @ Rs {item['base_price']}/kg")
        else:
            # Update price
            existing.base_price = item["base_price"]
            existing.available_quantity = item["available_quantity"]
            print(f"  Updated: {item['polymer_type']} {item['grade']}")

    db.commit()

    # --- 3. Pricing Rules ---
    pricing_rules = [
        {"location": "Ahmedabad", "freight_cost": 1.5,  "tax_percent": 0.18, "margin_percent": 0.05},
        {"location": "Rajkot",    "freight_cost": 2.0,  "tax_percent": 0.18, "margin_percent": 0.05},
        {"location": "Surat",     "freight_cost": 2.5,  "tax_percent": 0.18, "margin_percent": 0.05},
        {"location": "Vadodara",  "freight_cost": 2.0,  "tax_percent": 0.18, "margin_percent": 0.05},
        {"location": "Delhi",     "freight_cost": 5.0,  "tax_percent": 0.18, "margin_percent": 0.06},
        {"location": "Mumbai",    "freight_cost": 3.5,  "tax_percent": 0.18, "margin_percent": 0.05},
        {"location": "Mundra",    "freight_cost": 0.5,  "tax_percent": 0.18, "margin_percent": 0.04},
    ]
    for rule in pricing_rules:
        existing = db.query(PricingRule).filter(PricingRule.location == rule["location"]).first()
        if not existing:
            db.add(PricingRule(**rule))
            print(f"  Added pricing rule: {rule['location']}")
        else:
            print(f"  Pricing rule exists: {rule['location']}")

    db.commit()
    print("\n✅ Seed complete!")

except Exception as e:
    db.rollback()
    print(f"❌ Error: {e}")
    raise
finally:
    db.close()
