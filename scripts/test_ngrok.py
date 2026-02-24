import requests
import json

NGROK_URL = "https://faustino-postcruciate-unabortively.ngrok-free.dev/health"

def test_ngrok():
    print(f"--- Testing Ngrok URL: {NGROK_URL} ---")
    payload = {
        "call_id": "ngrok_test_123",
        "source": "manual_test_script"
    }
    
    try:
        print(f"Sending GET request...")
        response = requests.get(NGROK_URL, timeout=10)
        
        print(f"Status Code: {response.status_code}")
        print(f"Response Body: {response.text}")
        
        if response.status_code == 200:
            print("[SUCCESS] Ngrok Tunnel is ACTIVE and reaching your server!")
        else:
            print(f"[FAILURE] Endpoint reachable but returned error: {response.status_code}")
            
    except requests.exceptions.ConnectionError:
        print("[FAILURE] Could not connect to Ngrok URL. The tunnel might be down or URL is wrong.")
    except Exception as e:
        print(f"[ERROR] Request failed: {e}")

if __name__ == "__main__":
    test_ngrok()
