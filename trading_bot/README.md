# Futures Trading Bot — Binance Testnet

## Setup
1. Go to https://testnet.binancefuture.com and create API keys
2. Set these environment variables on Render:
   - BINANCE_FUTURES_API_KEY
   - BINANCE_FUTURES_SECRET
   - TELEGRAM_BOT_TOKEN
   - TELEGRAM_CHAT_ID

## Deploy
- Connect GitHub repo to Render
- Set start command: `python main.py`
- Add env vars above

## How it works
- Scans 20 coins every 15 minutes
- Uses Binance Futures Testnet (testnet.binancefuture.com)
- Reads REAL market data from futures testnet
- Places REAL orders on testnet wallet
- Leverage: 5x
- Max 15 open trades
- Dashboard auto-refreshes every 5 seconds
- Telegram alerts on every signal, TP hit, and SL hit
- Errors shown on dashboard AND sent to Telegram
