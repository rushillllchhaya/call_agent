from pyngrok import ngrok, conf
import os

def fix_ngrok():
    print("--- Fixing Ngrok Installation ---")
    try:
        # Check where pyngrok thinks the binary is
        print(f"Default binary path: {conf.get_default().ngrok_path}")
        
        # Force install
        print("Installing/Updating ngrok binary...")
        ngrok.install_ngrok()
        print("Installation complete.")
        
        # Verify
        if os.path.exists(conf.get_default().ngrok_path):
             print(f"[SUCCESS] Binary found at: {conf.get_default().ngrok_path}")
        else:
             print("[FAILURE] Binary still missing.")
             
    except Exception as e:
        print(f"[ERROR] {e}")

if __name__ == "__main__":
    fix_ngrok()
