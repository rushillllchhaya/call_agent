from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Float, Enum, JSON, Boolean, DECIMAL, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import datetime
from app.core.database import Base

# === Legacy Models (Kept for app logic/auth) ===
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    role = Column(String)  # "buyer" or "seller"
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    messages = Column(JSON)
    state = Column(JSON) # Persist ConversationState
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    user = relationship("User")

# class Deal(Base):
#     __tablename__ = "deals"
#     id = Column(Integer, primary_key=True, index=True)
#     user_id = Column(Integer, ForeignKey("users.id"))
#     polymer_type = Column(String)
#     grade = Column(String)
#     quantity = Column(Float)
#     price = Column(Float)
#     status = Column(String)
#     created_at = Column(DateTime(timezone=True), server_default=func.now())

# === New Schema Models ===

class Brand(Base):
    __tablename__ = "brands"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    code = Column(String, nullable=False)
    status = Column(String, default='1') # '1' Active
    order_no = Column(String, default='0')


class BrandGradeMap(Base):
    """
    Maps to 'brand_grade_maps' — the real lookup table in production.
    Each row = one grade_code (e.g. 'SG50') for a specific brand+polymer combo.
    Columns confirmed from SQL dump schema:
      id, name (grade_code string), grade_id (→ grades.id = polymer type),
      process_type_id, brand_id (→ brands.id), status
    """
    __tablename__ = "brand_grade_maps"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)          # e.g. "SG50", "H030 SG"
    grade_id = Column(String, nullable=False)      # FK → grades.id (polymer type)
    process_type_id = Column(String, default='0')
    brand_id = Column(String, nullable=False)      # FK → brands.id
    status = Column(String, default='1')           # '1' Active

class PolymerType(Base):
    """
    Maps to the 'grades' table in SQL.
    This table actually contains Polymer names like PVC, HDPE, etc.
    """
    __tablename__ = "grades"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False) # e.g., PVC, HDPE
    code = Column(String, nullable=False)
    hsn_code = Column(String)
    gst_rate = Column(String)
    status = Column(String, default='1')

class Buyer(Base):
    __tablename__ = "buyer_masters"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, default="BSB Buyer")
    company_name = Column(String, nullable=False)
    mobile_no = Column(String, nullable=False)
    email = Column(String, nullable=False)
    gst = Column(String)
    address = Column(String)
    city = Column(String, name="gst_city")
    state = Column(String, name="gst_state")
    status = Column(String, default='1')
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Seller(Base):
    __tablename__ = "seller_masters"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, default="BSB Seller")
    company_name = Column(String, nullable=False)
    mobile_no = Column(String, nullable=False)
    email = Column(Text, nullable=False)
    gst = Column(String)
    address = Column(String)
    city = Column(String, name="gst_city")
    state = Column(String, name="gst_state")
    status = Column(String, default='1')
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class ProductOffer(Base):
    """
    Maps to 'add_new_materials'.
    Represents a Sell Offer for a material.
    """
    __tablename__ = "add_new_materials"
    id = Column(Integer, primary_key=True, index=True)
    seller_id = Column(String, nullable=False) # Foreign Key logic handled manually or via cast if needed, since types differ in standard SQL
    
    # We map 'grade_id' in SQL to 'polymer_type_id' concept here because 'grades' table is PolymerType
    # But wait, looking at data: 
    # 'polymer_id' column exists. 'grade_id' column exists.
    # In 'grades' table (id=1 is PVC).
    # In 'add_new_materials': polymer_id='2', grade_id='1'.
    # This suggests 'polymer_id' might point to another table?
    # Let's stick to the names in the table for now to avoid confusion during mapping, 
    # but add properties for clarity.
    
    polymer_id = Column(String, nullable=False) 
    grade_id = Column(String, nullable=False) # This likely points to specific grade details if they exist, or is redundant.
    grade_code_id = Column(String)
    brand_id = Column(String)
    
    instock_in_kg = Column(Integer, default=0)
    moq_in_kg = Column(Integer, default=0)
    original_price = Column(DECIMAL(10, 2), default=0.00)
    bsb_margin_price = Column(DECIMAL(10, 2), default=0.25)
    # total_price is generated, so we might mark it as Computed or just ignore it for writes.
    # We will define it as regular column for reading.
    total_price = Column(DECIMAL(10, 2)) 
    
    offer_status_id = Column(String, default='1')
    status = Column(String, default='1')
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    offer_id = Column(String, nullable=False)
    buyer_id = Column(String, nullable=False)
    seller_id = Column(String, default='0')
    
    bid_price = Column(DECIMAL(10, 2), nullable=False)
    my_requirement_in_kg = Column(Integer, default=0) # Quantity
    
    buyer_grand_total = Column(DECIMAL(18, 2))
    order_status_id = Column(String, default='1')
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Lead(Base):
    __tablename__ = "leads"
    id           = Column(Integer, primary_key=True, index=True)
    customer_name = Column(String, nullable=True)   # caller's name
    mobile_no    = Column(String, nullable=True)    # caller's phone number
    intent       = Column(String, nullable=True)    # Buy / Sell
    polymer_type = Column(String, nullable=True)    # e.g. PP, PVC
    grade        = Column(String, nullable=True)    # e.g. H030 SG
    quantity     = Column(String, nullable=True)
    location     = Column(String, nullable=True)
    status       = Column(String, default="New")    # New | Qualified | Converted
    timestamp    = Column(DateTime(timezone=True), server_default=func.now())
    created_at   = Column(DateTime(timezone=True), server_default=func.now())


class GradeCode(Base):
    """
    Maps to the 'codes' table in the BSB production DB.
    Each row = one grade code (e.g. 'S60') belonging to a brand and polymer category.
    Columns: id, name (grade code string), grade_id (→ grades/polymer), brand_id (→ brands)
    """
    __tablename__ = "codes"
    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String, nullable=False)   # e.g. "S60", "TK1000"
    grade_id   = Column(String, nullable=False)   # FK → grades.id (polymer category)
    brand_id   = Column(String, nullable=False)   # FK → brands.id
    mfi_value  = Column(String, nullable=True)
    status     = Column(String, default='1')      # '1' = Active
    order_no   = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class PricingRule(Base):
    __tablename__ = "pricing_rules"
    id = Column(Integer, primary_key=True, index=True)
    location = Column(String, nullable=False, unique=True) # City or State
    freight_cost = Column(Float, default=0.0) # Per KG or Ton? Assuming Per KG for consistency with price
    tax_percent = Column(Float, default=0.18) # 18% GST default
    margin_percent = Column(Float, default=0.05) # 5% margin
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class PolymerInventory(Base):
    """
    Dedicated inventory table for BSB's own stock or aggregated stock.
    Can be used alongside ProductOffer.
    """
    __tablename__ = "polymer_inventory"
    id = Column(Integer, primary_key=True, index=True)
    polymer_type = Column(String, nullable=False) # e.g. PP
    grade = Column(String, nullable=False) # e.g. H030 SG
    available_quantity = Column(Float, default=0.0) # in MT or KG? Let's assume MT
    base_price = Column(Float, default=0.0) # Per KG
    warehouse_location = Column(String, default="Ahmedabad")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
