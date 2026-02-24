import sys
import os
sys.path.append(os.getcwd())
from fastapi.testclient import TestClient
from app.main import app

def test_websocket():
    print("Testing WebSocket connection to /ws/tata-audio...")
    client = TestClient(app)
    try:
        with client.websocket_connect("/ws/tata-audio") as websocket:
            print("Connected successfully!")
            websocket.send_text("Hello World")
            print("Sent text")
            # Close
            print("Closing...")
    except Exception as e:
        print(f"Failed: {e}")

if __name__ == "__main__":
    test_websocket()
