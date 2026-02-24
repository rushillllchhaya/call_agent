import urllib.request
import urllib.error

NGROK_URL = "https://faustino-postcruciate-unabortively.ngrok-free.dev/health"

def check_public_url():
    print(f"Checking {NGROK_URL}...")
    try:
        with urllib.request.urlopen(NGROK_URL) as response:
            print(f"Status: {response.status}")
            print(f"Body: {response.read().decode('utf-8')}")
    except urllib.error.HTTPError as e:
        print(f"HTTP Error: {e.code}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_public_url()
