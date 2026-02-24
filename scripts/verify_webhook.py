import sys
import os

# Ensure project root is in path (one level up)
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(project_root)

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_webhook():
    print("--- Testing Webhook Endpoint ---")
    payload = {
        "call_id": "test_call_123",
        "caller_id": "919876543210",
        "timestamp": "2024-01-01T12:00:00Z"
    }
    
    print(f"Sending POST to /webhook/incoming with payload: {payload}")
    try:
        response = client.post("/webhook/incoming", json=payload)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.json()}")
        
        if response.status_code == 200:
            print("[SUCCESS] Webhook endpoint reachable and handled request.")
        else:
            print(f"[FAILURE] Webhook endpoint returned error status: {response.status_code}")
            
    except Exception as e:
        print(f"[ERROR] Request failed: {e}")

if __name__ == "__main__":
    test_webhook()
