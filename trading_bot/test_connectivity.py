"""
Run this on Render to check which endpoints are accessible.
Add to startCommand temporarily or run via Render shell.
"""
import requests, sys

ENDPOINTS = [
    ("BinanceUS public klines", "https://api.binance.us/api/v3/klines",
     {"symbol": "BTCUSDT", "interval": "1h", "limit": "3"}),
    ("Binance.com public klines", "https://api.binance.com/api/v3/klines",
     {"symbol": "BTCUSDT", "interval": "1h", "limit": "3"}),
    ("Binance Vision public", "https://data-api.binance.vision/api/v3/klines",
     {"symbol": "BTCUSDT", "interval": "1h", "limit": "3"}),
    ("Binance Spot Testnet", "https://testnet.binance.vision/api/v3/time",
     {}),
]

print("=== Connectivity Test ===")
ok_count = 0
for name, url, params in ENDPOINTS:
    try:
        r = requests.get(url, params=params, timeout=8)
        if r.status_code == 200:
            print(f"✅ {name} -> OK")
            ok_count += 1
        else:
            print(f"❌ {name} -> HTTP {r.status_code}: {r.text[:80]}")
    except Exception as e:
        print(f"❌ {name} -> {e}")

print(f"\n{ok_count}/{len(ENDPOINTS)} endpoints reachable")
sys.exit(0 if ok_count > 0 else 1)
