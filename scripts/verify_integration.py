import sys
import os
import traceback

# Ensure project root is in path
sys.path.append(os.getcwd())

from app.core.database import Base, engine, SessionLocal
from app.core.models import Order, ProductOffer, PolymerType, Buyer, User
from app.services.orchestrator import Orchestrator

def setup_test_data(db):
    # Clear existing data for clean test? Or just append?
    # For now, let's just make sure our test data exists
    
    # Create Tables
    Base.metadata.create_all(bind=engine)
    
    # 1. Create Polymer (HDPE)
    # Check if exists
    hdpe = db.query(PolymerType).filter(PolymerType.name == "HDPE").first()
    if not hdpe:
        hdpe = PolymerType(name="HDPE", code="HDPE", status='1')
        db.add(hdpe)
        db.commit()
        db.refresh(hdpe)
        print(f"Created Polymer: HDPE (ID: {hdpe.id})")
    
    # 2. Create Product Offer
    # Check if exists
    offer = db.query(ProductOffer).filter(ProductOffer.polymer_id == str(hdpe.id)).first()
    if not offer:
        offer = ProductOffer(
            seller_id="TEST_SELLER_1",
            polymer_id=str(hdpe.id),
            grade_id="1",
            instock_in_kg=1000,
            original_price=95.0, # base price
            bsb_margin_price=5.0,
            total_price=100.0,
            status='1',
            offer_status_id='1'
        )
        db.add(offer)
        db.commit()
        print(f"Created Offer for HDPE at 100.0")

    # 3. Create User/Buyer
    user = db.query(User).filter(User.id == 999).first()
    if not user:
        user = User(id=999, role="buyer") # Legacy User model usage in orchestrator
        db.add(user)
        db.commit()
        print("Created Test User ID 999")
        
    return user.id

def test_flow():
    db = SessionLocal()
    try:
        user_id = setup_test_data(db)
        orchestrator = Orchestrator(db)
        
        print("\n--- Starting Test Flow ---")
        
        # 1. User says "I want to buy 100kg HDPE Grade 1 at 100 in Mumbai"
        msg1 = "I want to buy 100kg HDPE Grade 1 at 100 in Mumbai"
        resp1 = orchestrator.process_message(user_id, msg1)
        print(f"User: {msg1}")
        print(f"AI: {resp1}")
        
        # 2. User confirms "Yes"
        msg2 = "Yes, confirm deal"
        resp2 = orchestrator.process_message(user_id, msg2)
        print(f"User: {msg2}")
        print(f"AI: {resp2}")
        
        # 3. Verify Order Created
        # Get latest order
        order = db.query(Order).order_by(Order.id.desc()).first()
        
        # We check if we have an order and it matches roughly what we expect
        if order: 
            print(f"\n[INFO] Latest Order ID: {order.id}, Bid Price: {order.bid_price}, Buyer: {order.buyer_id}")
            
            if order.buyer_id == str(user_id) and float(order.bid_price) == 100.0:
                print("\n[SUCCESS] Order created in DB and matches requirements!")
            else:
                print("\n[FAILURE] Order details do not match.")
        else:
            print("\n[FAILURE] No order found in DB.")
                
    except Exception as e:
        print(f"[ERROR] {e}")
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    test_flow()
