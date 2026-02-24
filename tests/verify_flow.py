
import sys
import os
# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.database import SessionLocal, Base, engine
from app.services.orchestrator import Orchestrator
from app.core.models import User, Conversation, Lead, PricingRule, PolymerInventory
from sqlalchemy.orm import Session

# Setup mock data function
def setup_mock_data(db: Session):
    # Pricing Rules
    if not db.query(PricingRule).filter_by(location="Ahmedabad").first():
        db.add(PricingRule(location="Ahmedabad", freight_cost=1.5, tax_percent=0.18, margin_percent=0.05))
    if not db.query(PricingRule).filter_by(location="Delhi").first():
        db.add(PricingRule(location="Delhi", freight_cost=2.5, tax_percent=0.18, margin_percent=0.06))
    
    # Inventory
    if not db.query(PolymerInventory).filter_by(polymer_type="PP", grade="H030 SG").first():
        db.add(PolymerInventory(polymer_type="PP", grade="H030 SG", available_quantity=100, base_price=90.0, warehouse_location="Ahmedabad"))
    
    db.commit()

# Simulation function
def simulate_conversation():
    db = SessionLocal()
    # Ensure tables exist
    # Drop leads table to force recreate with new schema (since we changed the model)
    from sqlalchemy import text
    try:
        db.execute(text("DROP TABLE IF EXISTS leads"))
        db.commit()
    except Exception as e:
        print(f"Warning dropping table: {e}")

    Base.metadata.create_all(bind=engine)
    
    setup_mock_data(db)
    
    orchestrator = Orchestrator(db)
    user_id = 99999 # Test User ID
    
    # Clear previous state
    db.query(Conversation).filter_by(user_id=user_id).delete()
    # db.query(Lead).filter(Lead.customer_name.like("User%")).delete() # Cleanup leads? Maybe not
    db.commit()

    print("--- STARTING CONVERSATION SIMULATION ---")
    
    steps = [
        "Hi",
        "I want to buy polymers",
        "PP",
        "H030 SG",
        "10 tons",
        "Ahmedabad",
        "Yes confirm deal"
    ]
    
    for step in steps:
        print(f"\nUser: {step}")
        response = orchestrator.process_message(user_id, step, phone_number="9876543210")
        
        # Safe print for unicode (emojis)
        try:
            print(f"Bot: {response}")
        except UnicodeEncodeError:
            print(f"Bot: {response.encode('ascii', errors='ignore').decode('ascii')}")
        
        # Check logic assertions
        state = orchestrator._load_state(user_id)
        print(f"[State Debug] Stage: {state.stage} | Role: {state.role} | Details: {state.details}")

    print("--- END SIMULATION ---")
    db.close()

if __name__ == "__main__":
    simulate_conversation()
