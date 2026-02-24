from pyngrok import ngrok
import time

def start_tunnel():
    print("--- Starting Ngrok Tunnel ---")
    try:
        ngrok.set_auth_token("39WQ4hZDz3PkPo3EFHDtQQtukcd_GunYobbDZUfqNeLdPQxo")
        
        # Open a HTTP tunnel on the default port 8000
        public_url = ngrok.connect(8000).public_url
        print(f"\n[SUCCESS] Tunnel Started!\n")
        print(f"YOUR PUBLIC URL IS: {public_url}")
        print(f"WEBHOOK URL FOR TATA: {public_url}/webhook/incoming")
        print("\nKeep this script running to keep the tunnel open.")
        
        # Keep the process alive
        while True:
            time.sleep(1)
            
    except Exception as e:
        print(f"[ERROR] Could not start ngrok: {e}")
        print("Note: You may need to create a free account at ngrok.com and run 'ngrok config add-authtoken <TOKEN>' if prompted.")

if __name__ == "__main__":
    start_tunnel()
