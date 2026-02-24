from sqlalchemy.orm import Session
from sqlalchemy import func
from app.core.models import ProductOffer, PolymerType, PolymerInventory, PricingRule
import math

class PricingEngine:
    def __init__(self, db: Session):
        self.db = db
        # Fallback defaults if no specific rule found
        self.default_freight = 2.0 # Rs per kg
        self.default_tax = 0.18 # 18%
        self.default_margin = 0.05 # 5%

    def calculate_price(self, polymer: str, grade: str, quantity: float, location: str) -> dict:
        """
        Determines the final price based on:
        1. Base Price (from Inventory/Offers)
        2. Location-based Rules (Freight, Tax, Margin)
        
        Returns a dict with breakdown: {final_price, base_price, freight, tax, margin, currency}
        """
        # 1. Resolve Base Price
        # Try PolymerInventory first (Our stock)
        inventory_item = self.db.query(PolymerInventory).filter(
            PolymerInventory.polymer_type.ilike(polymer),
            PolymerInventory.grade.ilike(grade),
            PolymerInventory.available_quantity >= quantity
        ).first()

        base_price = 0.0
        
        if inventory_item:
            base_price = inventory_item.base_price
        else:
            # Fallback to ProductOffer (Marketplace offers)
            p_type = self.db.query(PolymerType).filter(PolymerType.name.ilike(polymer)).first()
            if p_type:
                 min_offer = self.db.query(func.min(ProductOffer.total_price)).filter(
                    ProductOffer.polymer_id == str(p_type.id),
                    ProductOffer.status == '1'
                ).scalar()
                 if min_offer:
                     base_price = float(min_offer)
        
        if base_price == 0.0:
            # Fail safe for prototype: Return a standard market indication
            # In production, this should return None to signal "No Quote"
            base_price = 100.0 

        # 2. Get Pricing Rules for Location
        rule = self.db.query(PricingRule).filter(PricingRule.location == location).first()
        
        freight = rule.freight_cost if rule else self.default_freight
        tax_pct = rule.tax_percent if rule else self.default_tax
        margin_pct = rule.margin_percent if rule else self.default_margin
        
        # 3. Compute Final Price
        # Formula: (Base + Freight) * (1 + Margin) * (1 + Tax) ?? 
        # User requirement: Final = Base + Freight + (Base * Tax) + Margin
        # Let's follow User Formula: final_price = base_price + freight_cost + (base_price * tax_percent) + margin
        # Wait, user wrote: "final_price = base_price + freight_cost + (base_price * tax_percent) + margin"
        # Margin is usually a percent of base or cost? User said "margin_percent" in table but used "+ margin" in formula.
        # I will assume Margin is a value calculated from percent: margin_value = base_price * margin_percent
        
        margin_value = base_price * margin_pct
        tax_value = base_price * tax_pct
        
        final_price = base_price + freight + tax_value + margin_value
        
        return {
            "final_price": round(final_price, 2),
            "base_price": base_price,
            "freight": freight,
            "tax": tax_value,
            "margin": margin_value,
            "currency": "INR",
            "found": base_price != 100.0 # Flag if we actually found real data
        }

    def validate_offer(self, polymer: str, grade: str, quantity: float, offer_price: float, location: str) -> bool:
        """
        For BUY flows: Users offers a price. We validate if it's acceptable.
        Accept if Offer >= Calculated Min Price (with lower margin?)
        """
        quote = self.calculate_price(polymer, grade, quantity, location)
        # Use a lower margin for "Acceptable" floor? Or strict?
        # Let's say we accept if it's within 2% of our calculation
        return offer_price >= (quote["final_price"] * 0.98)

