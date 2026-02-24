import sys
import os
import traceback
import time

# Ensure project root is in path
sys.path.append(os.getcwd())

from app.core.database import Base, engine, SessionLocal
from app.core.models import Order, ProductOffer, PolymerType, Buyer, User, Conversation
from app.services.orchestrator import Orchestrator

def setup_db(db):
    print("--- Setting up DB ---")
    # Force drop to apply new schema (state column)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    
    # Create Polymer
    hdpe = PolymerType(name="HDPE", code="HDPE", status='1')
    pvc = PolymerType(name="PVC", code="PVC", status='1')
    db.add_all([hdpe, pvc])
    db.commit()
    print("Created Polymers: HDPE, PVC")
    
    # Create User
    user = User(id=1, role="buyer") 
    db.add(user)
    db.commit()
    print("Created User ID 1")

def test_rag_and_persistence():
    db = SessionLocal()
    try:
        setup_db(db)
        
        # 1. Test RAG: "What do you have?"
        orchestrator = Orchestrator(db)
        print("\n--- Test 1: RAG (Inventory Query) ---")
        msg = "What do you trade?"
        resp = orchestrator.process_message(1, msg)
        print(f"User: {msg}")
        print(f"AI: {resp}")
        
        if "HDPE" in resp and "PVC" in resp:
            print("[SUCCESS] RAG returned available polymers.")
        else:
            print("[FAILURE] RAG did not return expected polymers.")

        # 2. Test Persistence: Multi-turn conversation
        print("\n--- Test 2: State Persistence ---")
        msg = "I want to buy HDPE"
        resp = orchestrator.process_message(1, msg)
        print(f"User: {msg}")
        print(f"AI: {resp}")
        
        # Simulate new request/instance to prove DB persistence
        # In a real app, the Orchestrator instance might persist or not, but state loading ensures it.
        # We'll rely on _load_state printing "Loaded existing state"
        
        msg = "100kg"
        resp = orchestrator.process_message(1, msg)
        print(f"User: {msg}")
        print(f"AI: {resp}")
        
        # Check DB directly for state
        conv = db.query(Conversation).filter(Conversation.user_id == 1).first()
        if conv and conv.state:
            print(f"[SUCCESS] Conversation state persisted in DB: {conv.state}")
            if "HDPE" in str(conv.state):
                 print("   - State correctly contains HDPE")
        else:
             print("[FAILURE] State not found in DB.")

        # 3. Test Role Switching
        print("\n--- Test 3: Dynamic Role Switching ---")
        # Current role should be BUYER (implied from "I want to buy")
        
        msg = "Actually I want to sell PVC"
        resp = orchestrator.process_message(1, msg)
        print(f"User: {msg}")
        print(f"AI: {resp}")
        
        # Verify role switch
        conv = db.query(Conversation).filter(Conversation.user_id == 1).first()
        state_dict = conv.state if isinstance(conv.state, dict) else {}
        
        if state_dict.get("role") == "seller":
            print(f"[SUCCESS] Role switched to SELLER. State: {state_dict}")
        else:
            print(f"[FAILURE] Role did not switch. State: {state_dict}")

    except Exception as e:
        print(f"[ERROR] {e}")
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    test_rag_and_persistence()
