# 🤖 Paper Trading Bot — Setup Guide

## ✅ What this does
- Scans 20 coins every 15 minutes on Binance Futures
- Auto places orders on **Binance Futures Testnet** (fake $100k USDT)
- Web dashboard shows live trades, P&L, signals
- Telegram alerts for every signal + close

---

## 📋 Step 1: Get Testnet API Keys

1. Go to **https://testnet.binancefuture.com**
2. Click **"Register"** (separate from real Binance)
3. Go to **API Management** → Create API key
4. Copy `API Key` and `Secret Key`

> ⚠️ Testnet gives you **$100,000 USDT fake money** automatically.

---

## 🚀 Step 2: Deploy on Render

1. Create GitHub repo, upload all these files
2. Go to **https://render.com** → New → Web Service
3. Connect your GitHub repo
4. Set **Start Command**: `python main.py`
5. Set **Environment Variables**:

```
BINANCE_TESTNET_API_KEY = <your testnet key>
BINANCE_TESTNET_SECRET  = <your testnet secret>
TELEGRAM_BOT_TOKEN      = <optional>
TELEGRAM_CHAT_ID        = <optional>
STATE_FILE              = /tmp/bot_state.json
```

6. Click **Deploy** ✅

---

## 🚀 Step 2 (Alternative): Deploy on Railway

1. Go to **https://railway.app** → New Project → Deploy from GitHub
2. Add same environment variables
3. Railway auto-detects `Procfile` → deploys

---

## 🌐 Step 3: Access Dashboard

After deploy, your dashboard URL will be:
- Render: `https://your-app-name.onrender.com`
- Railway: `https://your-app.up.railway.app`

Open it in browser — refreshes every 5 seconds automatically.

---

## 📊 Dashboard shows:
- 💰 Live balance + P&L + ROI
- 📈 Open trades (coin, direction, score, entry/SL/TP)
- ✅ Closed trades (WIN/LOSS/BREAKEVEN + P&L)
- 📋 Live bot log
- 🏆 Win rate + stats

---

## ⚙️ Customize in bot.py

```python
MIN_SCORE       = 6      # Minimum signal score (6-10)
MAX_SIGNALS_DAY = 15     # Max new trades per day
MAX_OPEN_TRADES = 8      # Max concurrent open trades
MIN_RR          = 1.8    # Min risk:reward ratio

SCORE_ALLOC = {
    6:  4.0,   # Score 6 → 4% of available balance
    7:  6.0,   # Score 7 → 6%
    8:  9.0,   # Score 8 → 9%
    9:  14.0,  # Score 9 → 14%
    10: 18.0,  # Score 10 → 18%
}
```

---

## 🔄 Confidence Escalation
If a coin already has an open trade and a **stronger signal** appears,
the bot **adds more allocation** (pyramid into winner) instead of opening a new trade.

---

## ⚠️ Important Notes
- This uses **Binance Futures Testnet** — all trades are FAKE money
- Real Binance URLs are only used to fetch market data (klines) — no real orders
- State is saved to `/tmp/bot_state.json` — resets on Render restart (free tier)
- For persistent state on Render, upgrade to paid plan or use a DB

---

## 📁 Files
```
main.py          ← Entry point (runs bot + dashboard)
bot.py           ← Signal engine + auto trading logic
dashboard.py     ← Flask web dashboard
requirements.txt ← Python dependencies
Procfile         ← Render/Railway start command
.env.example     ← Environment variables template
```
