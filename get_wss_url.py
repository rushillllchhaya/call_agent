#!/usr/bin/env python3
"""
get_wss_url.py — Fetches the live ngrok tunnel URL and prints the WSS endpoint.
Run this AFTER `docker compose up -d` to get the URL for TATA SmartFlo.

Usage:
    python get_wss_url.py
"""
import json
import sys
import time
import urllib.request

NGROK_API = "http://localhost:4040/api/tunnels"
MAX_WAIT   = 30   # seconds


def fetch_url() -> str | None:
    try:
        with urllib.request.urlopen(NGROK_API, timeout=5) as r:
            data = json.loads(r.read())
        for tunnel in data.get("tunnels", []):
            pub = tunnel.get("public_url", "")
            if pub.startswith("https://"):
                return pub
    except Exception:
        return None


def main():
    print("⏳ Waiting for ngrok tunnel...")
    for _ in range(MAX_WAIT):
        url = fetch_url()
        if url:
            wss = url.replace("https://", "wss://") + "/ws/tata-audio"
            dash = url + "/dashboard/index.html"
            print("\n" + "=" * 60)
            print("✅  BSB Voice Agent is live!")
            print("=" * 60)
            print(f"\n📡  TATA SmartFlo WebSocket Endpoint:")
            print(f"    {wss}")
            print(f"\n🖥️  Live Dashboard:")
            print(f"    {dash}")
            print(f"\n🔧  ngrok Inspector:")
            print(f"    http://localhost:4040")
            print("=" * 60)
            print("\n📋  TATA SmartFlo Settings:")
            print(f"    Endpoint Type  : Static")
            print(f"    Endpoint (WSS) : {wss}")
            print(f"    Header         : (none required)")
            print(f"    Status         : Enabled")
            print("=" * 60 + "\n")
            return
        time.sleep(1)
    print("❌  ngrok did not start in time. Check: docker compose logs ngrok")
    sys.exit(1)


if __name__ == "__main__":
    main()
