import time, os, json, threading, logging, hmac, hashlib
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger("BOT")

# ═══════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════
API_KEY    = os.environ.get("BINANCE_FUTURES_API_KEY", "")
API_SECRET = os.environ.get("BINANCE_FUTURES_SECRET",  "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID",   "")

FUTURES_BASE = "https://testnet.binancefuture.com"
STATE_FILE   = os.environ.get("STATE_FILE", "/tmp/bot_state.json")

# Sri Lanka timezone (UTC+5:30)
SL_TZ = timezone(timedelta(hours=5, minutes=30))

def now_sl():
    """Current time in Sri Lanka timezone."""
    return datetime.now(SL_TZ)

def ts_sl():
    """Timestamp string in SL time."""
    return now_sl().strftime("%H:%M:%S")

def dt_sl():
    """Datetime string in SL time."""
    return now_sl().strftime("%Y-%m-%d %H:%M")

def today_sl():
    """Today's date string in SL time."""
    return now_sl().strftime("%Y-%m-%d")

WATCH_LIST = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT",
    "ADAUSDT","LINKUSDT","AVAXUSDT","DOTUSDT","MATICUSDT",
    "LTCUSDT","UNIUSDT","NEARUSDT","APTUSDT","INJUSDT",
    "OPUSDT","ARBUSDT","SUIUSDT","TIAUSDT","FETUSDT"
]

MIN_SCORE       = 10
MAX_SIGNALS_DAY = 15
MAX_OPEN_TRADES = 15
MIN_RR          = 1.8
DATA_LIMIT      = 300
LEVERAGE        = 1
SCORE_ALLOC_PCT = {4:2.0, 5:3.0, 6:5.0, 7:7.0, 8:9.0, 9:12.0, 10:15.0, 11:15.0}
MAX_ALLOC_PCT   = 0.15
MIN_TRADE_USDT  = 10.0

# ═══════════════════════════════════════════════
# SESSION
# ═══════════════════════════════════════════════
def make_session():
    s = requests.Session()
    r = Retry(total=4, backoff_factor=1.5,
              status_forcelist=[429,500,502,503,504],
              allowed_methods=["GET","POST","DELETE"])
    s.mount("https://", HTTPAdapter(max_retries=r))
    return s

SESSION = make_session()
_lock   = threading.Lock()

# ═══════════════════════════════════════════════
# STATE
# ═══════════════════════════════════════════════
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {
            "balance": 0.0, "wallet_balance": 0.0,
            "open_trades": [], "closed_trades": [],
            "signals_today": 0, "signals_date": "",
            "bot_status": "starting", "last_scan": "",
            "wins": 0, "losses": 0, "bes": 0,
            "total_pnl": 0.0, "log": [],
            "errors": [],
            "bot_running": True,
            "pending_orders_count": 0,
        }

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)

def add_log(state, msg, level="INFO"):
    state["log"].insert(0, {"ts": ts_sl(), "msg": msg, "level": level})
    state["log"] = state["log"][:300]
    log.info(msg)

def add_error(state, msg):
    state["errors"].insert(0, {"ts": dt_sl(), "msg": msg})
    state["errors"] = state["errors"][:50]
    add_log(state, f"❗ {msg}", "ERROR")

# ═══════════════════════════════════════════════
# TELEGRAM — actual trades only
# ═══════════════════════════════════════════════
def tg(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chunk in [msg[i:i+4000] for i in range(0, len(msg), 4000)]:
        try:
            SESSION.post(url, json={
                "chat_id": TELEGRAM_CHAT, "text": chunk, "parse_mode": "Markdown"
            }, timeout=8)
        except Exception as e:
            log.warning(f"TG error: {e}")
        time.sleep(0.2)

def tg_error(msg):
    tg(f"❗ *BOT ERROR*\n`{msg}`")

# ═══════════════════════════════════════════════
# BINANCE FUTURES TESTNET API
# ═══════════════════════════════════════════════
def sign(params: dict) -> str:
    query = urlencode(params)
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def fapi_get(path, params=None, signed=False):
    if params is None:
        params = {}
    if signed:
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = sign(params)
    headers = {"X-MBX-APIKEY": API_KEY}
    try:
        r = SESSION.get(f"{FUTURES_BASE}{path}", params=params,
                        headers=headers, timeout=12)
        data = r.json()
        if isinstance(data, dict) and "code" in data and data["code"] != 200:
            log.error(f"fapi GET {path} error: {data}")
            return None
        return data
    except Exception as e:
        log.error(f"fapi GET {path} exception: {e}")
        return None

def fapi_post(path, params):
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params)
    headers = {"X-MBX-APIKEY": API_KEY}
    try:
        r = SESSION.post(f"{FUTURES_BASE}{path}", params=params,
                         headers=headers, timeout=12)
        return r.json()
    except Exception as e:
        log.error(f"fapi POST {path} exception: {e}")
        return {"error": str(e)}

def fapi_post_algo(params):
    """POST /fapi/v1/algoOrder — required for STOP/TAKE_PROFIT conditional orders since 2025-12-09."""
    params["algoType"] = "CONDITIONAL"
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params)
    headers = {"X-MBX-APIKEY": API_KEY}
    try:
        r = SESSION.post(f"{FUTURES_BASE}/fapi/v1/algoOrder", params=params,
                         headers=headers, timeout=12)
        return r.json()
    except Exception as e:
        log.error(f"fapi ALGO POST exception: {e}")
        return {"error": str(e)}

def fapi_delete_algo(algo_id):
    """DELETE /fapi/v1/algoOrder — cancel a conditional order by algoId."""
    params = {"algoId": algo_id, "timestamp": int(time.time() * 1000)}
    params["signature"] = sign(params)
    headers = {"X-MBX-APIKEY": API_KEY}
    try:
        r = SESSION.delete(f"{FUTURES_BASE}/fapi/v1/algoOrder", params=params,
                           headers=headers, timeout=12)
        return r.json()
    except Exception as e:
        log.error(f"fapi ALGO DELETE exception: {e}")
        return {"error": str(e)}

def fapi_delete(path, params):
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params)
    headers = {"X-MBX-APIKEY": API_KEY}
    try:
        r = SESSION.delete(f"{FUTURES_BASE}{path}", params=params,
                           headers=headers, timeout=12)
        return r.json()
    except Exception as e:
        log.error(f"fapi DELETE {path} exception: {e}")
        return {"error": str(e)}

# ═══════════════════════════════════════════════
# WALLET & MARKET DATA
# ═══════════════════════════════════════════════
def get_wallet_balance():
    data = fapi_get("/fapi/v2/account", {}, signed=True)
    if data and "assets" in data:
        for a in data["assets"]:
            if a.get("asset") == "USDT":
                return float(a.get("availableBalance", 0)), float(a.get("walletBalance", 0))
    data2 = fapi_get("/fapi/v2/balance", {}, signed=True)
    if isinstance(data2, list):
        for b in data2:
            if b.get("asset") == "USDT":
                return float(b.get("availableBalance", 0)), float(b.get("balance", 0))
    return None, None

def get_binance_positions():
    """Fetch all open positions from Binance (non-zero positionAmt)."""
    data = fapi_get("/fapi/v2/positionRisk", {}, signed=True)
    if not isinstance(data, list):
        return []
    return [p for p in data if abs(float(p.get("positionAmt", 0))) > 0]

def get_binance_open_orders():
    """Fetch all open orders (SL/TP pending) from Binance."""
    data = fapi_get("/fapi/v1/openOrders", {}, signed=True)
    if isinstance(data, list):
        return data
    return []

def get_all_open_orders_count():
    data = fapi_get("/fapi/v1/openOrders", {}, signed=True)
    return len(data) if isinstance(data, list) else 0

def get_klines(symbol, interval="1h", limit=300):
    data = fapi_get("/fapi/v1/klines", {
        "symbol": symbol, "interval": interval, "limit": limit
    })
    if not data or isinstance(data, dict):
        return None
    df = pd.DataFrame(data, columns=[
        'Open_time','open','high','low','close','volume',
        'Close_time','qav','num_trades','taker_base','taker_quote','ignore'])
    for c in ['open','high','low','close','volume']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df['Open_time'] = df['Open_time'].astype(int)
    df = df.dropna(subset=['open','high','low','close'])
    return df.reset_index(drop=True) if len(df) >= 50 else None

def get_price(symbol):
    data = fapi_get("/fapi/v1/ticker/price", {"symbol": symbol})
    if data and "price" in data:
        return float(data["price"])
    return None

def get_htf_trend(symbol):
    scores = {"BULL": 0, "BEAR": 0}
    for tf, lim, w in [("4h", 80, 2), ("1d", 40, 1)]:
        df = get_klines(symbol, tf, lim)
        if df is None or len(df) < 40:
            continue
        c = df['close']
        e20 = c.ewm(span=20, adjust=False).mean()
        e50 = c.ewm(span=50, adjust=False).mean()
        lc  = c.iloc[-1]
        if lc > e20.iloc[-1] > e50.iloc[-1]:   scores["BULL"] += w
        elif lc < e20.iloc[-1] < e50.iloc[-1]: scores["BEAR"] += w
        time.sleep(0.05)
    if scores["BULL"] >= 2: return "BULL", min(scores["BULL"], 3)
    if scores["BEAR"] >= 2: return "BEAR", min(scores["BEAR"], 3)
    return "NEUTRAL", 0

def get_exchange_info(symbol):
    data = fapi_get("/fapi/v1/exchangeInfo")
    if not data:
        return None
    for s in data.get("symbols", []):
        if s["symbol"] == symbol:
            info = {"minQty": 0.001, "stepSize": 0.001, "tickSize": 0.01, "minNotional": 5}
            for f in s.get("filters", []):
                if f["filterType"] == "LOT_SIZE":
                    info["minQty"]   = float(f["minQty"])
                    info["stepSize"] = float(f["stepSize"])
                if f["filterType"] == "MIN_NOTIONAL":
                    info["minNotional"] = float(f.get("notional", 5))
                if f["filterType"] == "PRICE_FILTER":
                    info["tickSize"] = float(f["tickSize"])
            return info
    return None

def round_step(value, step):
    import math
    precision = max(0, -int(math.floor(math.log10(step))))
    return round(round(value / step) * step, precision)

def set_leverage(symbol, leverage=LEVERAGE):
    resp = fapi_post("/fapi/v1/leverage", {
        "symbol": symbol, "leverage": leverage
    })
    if isinstance(resp, dict) and "leverage" in resp:
        return True
    log.warning(f"set_leverage {symbol} failed: {resp}")
    return False

# ═══════════════════════════════════════════════
# SYNC STATE WITH BINANCE POSITIONS
# Binance eke actual positions state eke sync කිරීම
# ═══════════════════════════════════════════════
def sync_positions_with_binance(state):
    """
    Binance ලා open positions state ලා open_trades සමඟ compare කරලා,
    state ලා නෑ නම් add කරනවා. Binance ලා close වෙලා state ලා තියෙනවා
    නම් closed_trades ට move කරනවා.
    """
    try:
        binance_positions = get_binance_positions()
        binance_symbols = set()

        for pos in binance_positions:
            sym = pos["symbol"]
            amt = float(pos["positionAmt"])
            direction = "LONG" if amt > 0 else "SHORT"
            entry_price = float(pos.get("entryPrice", 0))
            binance_symbols.add(sym)

            # State ලා ඒ coin+direction trade නෑ නම් add කරනවා
            existing = [t for t in state["open_trades"]
                        if t["symbol"] == sym and t["direction"] == direction]
            if not existing and entry_price > 0:
                qty = abs(amt)

                # ── SL/TP calculate කිරීම ──
                sl_val = tp1_val = tp2_val = 0.0
                sl_pct_val = tp1_pct_val = rr_val = 0.0
                sl_order_id = tp1_order_id = tp2_order_id = ""
                half_qty = qty

                try:
                    df_raw = get_klines(sym, "1h", DATA_LIMIT)
                    if df_raw is not None and len(df_raw) >= 50:
                        df_ind = compute_indicators(df_raw)
                        i = len(df_ind) - 1
                        sl_val, tp1_val, tp2_val, sl_pct_val, tp1_pct_val, rr_val = \
                            calculate_levels(df_ind, i, direction, entry_price)

                        # Binance ලා SL + TP1 orders place කිරීම (closePosition=true only)
                        info = get_exchange_info(sym)
                        if info and sl_val > 0:
                            close_side = "SELL" if direction == "LONG" else "BUY"
                            sl_price  = round_step(sl_val,  info["tickSize"])
                            tp1_price = round_step(tp1_val, info["tickSize"])
                            half_qty  = qty  # full qty for closePosition orders

                            # SL — algo endpoint
                            sl_resp = fapi_post_algo({
                                "symbol":        sym,
                                "side":          close_side,
                                "type":          "STOP_MARKET",
                                "triggerPrice":     sl_price,
                                "closePosition": "true",
                                "positionSide":  "BOTH",
                                "timeInForce":   "GTC",
                            })
                            if isinstance(sl_resp, dict) and "algoId" in sl_resp:
                                sl_order_id = str(sl_resp["algoId"])
                                add_log(state, f"✅ SYNC SL placed {sym} @ {sl_price}")
                            else:
                                add_error(state, f"SYNC SL failed {sym}: {sl_resp}")
                            time.sleep(0.3)

                            # TP1 — algo endpoint (no reduceOnly on testnet)
                            tp1_resp = fapi_post_algo({
                                "symbol":        sym,
                                "side":          close_side,
                                "type":          "TAKE_PROFIT_MARKET",
                                "triggerPrice":     tp1_price,
                                "closePosition": "true",
                                "positionSide":  "BOTH",
                                "timeInForce":   "GTC",
                            })
                            if isinstance(tp1_resp, dict) and "algoId" in tp1_resp:
                                tp1_order_id = str(tp1_resp["algoId"])
                                add_log(state, f"✅ SYNC TP1 placed {sym} @ {tp1_price}")
                            else:
                                add_error(state, f"SYNC TP1 failed {sym}: {tp1_resp}")
                            # TP2 = software monitor only (no separate Binance order)
                except Exception as e:
                    add_error(state, f"SYNC SL/TP calc error {sym}: {e}")

                phantom_trade = {
                    "id":           f"{sym}_{direction}_SYNCED_{int(time.time())}",
                    "symbol":       sym,
                    "direction":    direction,
                    "score":        0,
                    "entry":        entry_price,
                    "sl":           round(sl_val, 6),
                    "tp1":          round(tp1_val, 6),
                    "tp2":          round(tp2_val, 6),
                    "sl_pct":       round(sl_pct_val, 3),
                    "tp1_pct":      round(tp1_pct_val, 3),
                    "rr":           round(rr_val, 2),
                    "alloc_usd":    qty * entry_price,
                    "alloc_pct":    0.0,
                    "notional":     qty * entry_price,
                    "qty":          qty,
                    "half_qty":     half_qty,
                    "leverage":     LEVERAGE,
                    "htf":          "SYNC",
                    "reasons":      ["Synced from Binance"],
                    "open_time":    dt_sl(),
                    "order_id":     "SYNCED",
                    "sl_order_id":  sl_order_id,
                    "tp_order_id":  tp1_order_id,
                    "tp2_order_id": tp2_order_id,
                    "tp1_hit":      False,
                    "outcome":      "OPEN",
                }
                state["open_trades"].append(phantom_trade)
                add_log(state, f"🔄 SYNC: Added {sym} {direction} entry=${entry_price:.4f} SL={sl_val:.4f} TP1={tp1_val:.4f}")

        # State ලා open නම් Binance ලා නෑ නම් → auto-closed
        to_remove = []
        for trade in state["open_trades"]:
            sym       = trade["symbol"]
            direction = trade["direction"]
            # Check if this symbol+direction exists in binance
            match = any(
                p["symbol"] == sym and
                (("LONG" if float(p["positionAmt"]) > 0 else "SHORT") == direction)
                for p in binance_positions
            )
            if not match:
                # Position Binance ලා close වෙලා
                price = get_price(sym) or trade["entry"]
                entry = trade["entry"]
                alloc = trade.get("alloc_usd", 0)
                notional = trade.get("notional", alloc)
                if direction == "LONG":
                    pnl_pct = (price - entry) / entry * 100 if entry > 0 else 0
                else:
                    pnl_pct = (entry - price) / entry * 100 if entry > 0 else 0
                pnl_usd = round(notional * pnl_pct / 100, 2)

                state["total_pnl"] = round(state.get("total_pnl", 0) + pnl_usd, 2)
                if pnl_usd > 0:   state["wins"]   += 1
                elif pnl_usd < 0: state["losses"] += 1
                else:             state["bes"]     += 1

                trade["outcome"]    = "AUTO_CLOSED"
                trade["pnl_usd"]    = pnl_usd
                trade["pnl_pct"]    = round(pnl_pct, 3)
                trade["close_time"] = dt_sl()
                trade["exit_price"] = price
                state["closed_trades"].insert(0, trade)
                state["closed_trades"] = state["closed_trades"][:500]
                to_remove.append(trade["id"])
                em = "✅" if pnl_usd >= 0 else "❌"
                add_log(state, f"{em} AUTO_CLOSED {sym} {direction} | P&L: ${pnl_usd:+.2f}")

        if to_remove:
            state["open_trades"] = [t for t in state["open_trades"] if t["id"] not in to_remove]

    except Exception as e:
        add_error(state, f"sync_positions error: {e}")

    return state

# ═══════════════════════════════════════════════
# INDICATORS
# ═══════════════════════════════════════════════
def compute_indicators(df):
    d = df.copy()
    d['EMA8']   = d['close'].ewm(span=8,   adjust=False).mean()
    d['EMA21']  = d['close'].ewm(span=21,  adjust=False).mean()
    d['EMA55']  = d['close'].ewm(span=55,  adjust=False).mean()
    d['EMA200'] = d['close'].ewm(span=200, adjust=False).mean()

    delta = d['close'].diff()
    gain  = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss  = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    d['RSI'] = 100 - (100 / (1 + gain / (loss + 1e-9)))

    e12 = d['close'].ewm(span=12, adjust=False).mean()
    e26 = d['close'].ewm(span=26, adjust=False).mean()
    d['MACD']      = e12 - e26
    d['MACDS']     = d['MACD'].ewm(span=9, adjust=False).mean()
    d['MACD_HIST'] = d['MACD'] - d['MACDS']

    d['BB_MA']    = d['close'].rolling(20).mean()
    d['BB_STD']   = d['close'].rolling(20).std()
    d['BB_UP']    = d['BB_MA'] + 2 * d['BB_STD']
    d['BB_LO']    = d['BB_MA'] - 2 * d['BB_STD']
    d['BB_WIDTH'] = (d['BB_UP'] - d['BB_LO']) / (d['BB_MA'] + 1e-9)
    d['VOL_MA20'] = d['volume'].rolling(20).mean()

    hl  = d['high'] - d['low']
    hpc = (d['high'] - d['close'].shift(1)).abs()
    lpc = (d['low']  - d['close'].shift(1)).abs()
    d['ATR'] = pd.concat([hl, hpc, lpc], axis=1).max(axis=1).rolling(14).mean()

    rsi_min = d['RSI'].rolling(14).min()
    rsi_max = d['RSI'].rolling(14).max()
    d['STOCHRSI'] = (d['RSI'] - rsi_min) / ((rsi_max - rsi_min) + 1e-9)

    pdm  = d['high'].diff().clip(lower=0)
    mdm  = (-d['low'].diff()).clip(lower=0)
    pdm2 = pdm.where(pdm > mdm, 0)
    mdm2 = mdm.where(mdm > pdm, 0)
    d['PLUS_DI']  = 100 * (pdm2.rolling(14).mean() / (d['ATR'] + 1e-9))
    d['MINUS_DI'] = 100 * (mdm2.rolling(14).mean() / (d['ATR'] + 1e-9))
    dx = 100 * (d['PLUS_DI'] - d['MINUS_DI']).abs() / (d['PLUS_DI'] + d['MINUS_DI'] + 1e-9)
    d['ADX'] = dx.rolling(14).mean()

    hl2 = (d['high'] + d['low']) / 2
    ub  = hl2 + 3.0 * d['ATR']
    lb  = hl2 - 3.0 * d['ATR']
    st_dir = [1] * len(d); st_val = [0.0] * len(d)
    for i in range(1, len(d)):
        fub = ub.iloc[i] if ub.iloc[i] < ub.iloc[i-1] or d['close'].iloc[i-1] > ub.iloc[i-1] else ub.iloc[i-1]
        flb = lb.iloc[i] if lb.iloc[i] > lb.iloc[i-1] or d['close'].iloc[i-1] < lb.iloc[i-1] else lb.iloc[i-1]
        if st_val[i-1] == ub.iloc[i-1]:
            st_dir[i] = -1 if d['close'].iloc[i] <= fub else 1
            st_val[i] = fub if d['close'].iloc[i] <= fub else flb
        else:
            st_dir[i] = 1 if d['close'].iloc[i] >= flb else -1
            st_val[i] = flb if d['close'].iloc[i] >= flb else fub
    d['ST_DIR'] = st_dir; d['ST_VAL'] = st_val

    # ── Ichimoku (9/26/52) ──
    h9  = d['high'].rolling(9).max();  l9  = d['low'].rolling(9).min()
    h26 = d['high'].rolling(26).max(); l26 = d['low'].rolling(26).min()
    h52 = d['high'].rolling(52).max(); l52 = d['low'].rolling(52).min()
    d['ICH_TENKAN']  = (h9  + l9)  / 2
    d['ICH_KIJUN']   = (h26 + l26) / 2
    d['ICH_SPAN_A']  = ((d['ICH_TENKAN'] + d['ICH_KIJUN']) / 2).shift(26)
    d['ICH_SPAN_B']  = ((h52 + l52) / 2).shift(26)

    # ── VWAP (session-level approximation: cumulative from start of df) ──
    tp_vwap = (d['high'] + d['low'] + d['close']) / 3
    d['VWAP'] = (tp_vwap * d['volume']).cumsum() / (d['volume'].cumsum() + 1e-9)

    # ── Pivot-based Support / Resistance (last 20 bars swing highs/lows) ──
    # SR_NEAR=1 means price within 0.5% of a swing level; SR_AT=1 means within 0.2%
    swing_highs = d['high'].rolling(5, center=True).max()
    swing_lows  = d['low'].rolling(5, center=True).min()
    sr_near = [0] * len(d); sr_at = [0] * len(d)
    for k in range(len(d)):
        cl = d['close'].iloc[k]
        lb2 = max(0, k - 20)
        levels = list(swing_highs.iloc[lb2:k]) + list(swing_lows.iloc[lb2:k])
        levels = [x for x in levels if not pd.isna(x)]
        if levels:
            dists = [abs(cl - lv) / (cl + 1e-9) for lv in levels]
            mn = min(dists)
            if mn <= 0.002:  sr_at[k]   = 1
            elif mn <= 0.005: sr_near[k] = 1
    d['SR_AT']   = sr_at
    d['SR_NEAR'] = sr_near

    # ── Candle patterns (last 2 bars) ──
    o = d['open']; h = d['high']; l = d['low']; c2 = d['close']
    body   = (c2 - o).abs()
    rng    = (h - l).replace(0, 1e-9)
    body_r = body / rng  # body-to-range ratio
    upper_shadow = h - c2.where(c2 >= o, o)
    lower_shadow = c2.where(c2 <= o, o) - l

    # Bullish engulfing: prev bar bearish, current bar bullish and body engulfs prev
    bull_engulf = ((c2.shift(1) < o.shift(1)) &   # prev bearish
                   (c2 > o) &                       # current bullish
                   (o <= c2.shift(1)) &             # current open <= prev close
                   (c2 >= o.shift(1)))              # current close >= prev open
    # Bearish engulfing
    bear_engulf = ((c2.shift(1) > o.shift(1)) &
                   (c2 < o) &
                   (o >= c2.shift(1)) &
                   (c2 <= o.shift(1)))
    # Hammer (bullish): small body at top, long lower shadow ≥ 2×body
    hammer = ((lower_shadow >= 2 * body) & (upper_shadow <= 0.3 * body) & (c2 >= o))
    # Shooting star (bearish): small body at bottom, long upper shadow
    shooting_star = ((upper_shadow >= 2 * body) & (lower_shadow <= 0.3 * body) & (c2 <= o))
    # 3 white soldiers: 3 consecutive bullish candles, each closing higher
    soldiers = (c2 > o) & (c2.shift(1) > o.shift(1)) & (c2.shift(2) > o.shift(2)) & \
               (c2 > c2.shift(1)) & (c2.shift(1) > c2.shift(2))
    # 3 black crows
    crows = (c2 < o) & (c2.shift(1) < o.shift(1)) & (c2.shift(2) < o.shift(2)) & \
            (c2 < c2.shift(1)) & (c2.shift(1) < c2.shift(2))
    # Inside bar (IB): current bar entirely within previous bar range
    inside_bar = (h < h.shift(1)) & (l > l.shift(1))

    d['BULL_ENGULF']  = bull_engulf.astype(int)
    d['BEAR_ENGULF']  = bear_engulf.astype(int)
    d['HAMMER']       = hammer.astype(int)
    d['SHOOT_STAR']   = shooting_star.astype(int)
    d['SOLDIERS']     = soldiers.astype(int)
    d['CROWS']        = crows.astype(int)
    d['INSIDE_BAR']   = inside_bar.astype(int)

    # ── Market structure: HH+HL (bull) / LH+LL (bear) over last 10 bars ──
    ms_bull = [0] * len(d); ms_bear = [0] * len(d)
    for k in range(3, len(d)):
        lb3 = max(0, k - 10)
        highs = list(d['high'].iloc[lb3:k+1])
        lows  = list(d['low'].iloc[lb3:k+1])
        if len(highs) >= 4:
            hh = highs[-1] > highs[-2] > highs[-3]
            hl = lows[-1]  > lows[-2]  > lows[-3]
            lh = highs[-1] < highs[-2] < highs[-3]
            ll = lows[-1]  < lows[-2]  < lows[-3]
            if hh and hl: ms_bull[k] = 1
            if lh and ll:  ms_bear[k] = 1
    d['MS_BULL'] = ms_bull
    d['MS_BEAR'] = ms_bear

    return d

# ═══════════════════════════════════════════════
# SIGNAL SCORING
# ═══════════════════════════════════════════════
def score_signal(df, i, direction, htf_trend, htf_strength):
    """
    13-category score engine — mirrors backtest_signal_flow.svg exactly.
    Minimum to trade: 10 pts  (MIN_SCORE = 10)
    """
    c    = df.iloc[i]
    prev = df.iloc[i - 1]
    p2   = df.iloc[i - 2] if i >= 2 else prev
    score = 0; reasons = []

    # ── 1. HTF Trend ── max 3 ──────────────────────────────────────────
    if direction == "LONG" and htf_trend == "BULL":
        score += htf_strength; reasons.append(f"HTF BULL +{htf_strength}")
    elif direction == "SHORT" and htf_trend == "BEAR":
        score += htf_strength; reasons.append(f"HTF BEAR +{htf_strength}")
    elif htf_trend == "NEUTRAL":
        score += 1; reasons.append("HTF neutral")
    else:
        return 0, ["HTF counter-trend"]

    # ── 2. ADX ── max 3 ────────────────────────────────────────────────
    adx = float(c.get('ADX', 0) or 0)
    if adx < 18: return 0, [f"ADX {adx:.1f} flat"]
    if adx >= 30:   score += 3; reasons.append(f"ADX {adx:.1f} strong")
    elif adx >= 25: score += 2; reasons.append(f"ADX {adx:.1f} ok")
    else:           score += 1; reasons.append(f"ADX {adx:.1f}")

    # ── 3. Supertrend ── max 2 (match +2, mismatch −1) ─────────────────
    st = int(c.get('ST_DIR', 0) or 0)
    if direction == "LONG":
        if st == 1:  score += 2; reasons.append("ST bull ✓")
        else:        score -= 1; reasons.append("ST bear ⚠")
    else:
        if st == -1: score += 2; reasons.append("ST bear ✓")
        else:        score -= 1; reasons.append("ST bull ⚠")

    # ── 4. EMA stack ── max 4 ──────────────────────────────────────────
    cl    = float(c['close']); prev_cl = float(prev['close'])
    e8    = float(c['EMA8']);   e21  = float(c['EMA21'])
    e55   = float(c['EMA55']); e200 = float(c['EMA200'])
    if direction == "LONG":
        if cl > e8 > e21 > e55 > e200:   score += 4; reasons.append("EMA perfect bull")
        elif cl > e21 > e55 > e200:       score += 3; reasons.append("EMA full bull")
        elif cl > e21 > e55:              score += 2; reasons.append("EMA 21>55")
        elif cl > e21:                    score += 1; reasons.append("Above EMA21")
        if prev_cl < e8 and cl > e8:      score += 1; reasons.append("EMA8 cross ↑")
    else:
        if cl < e8 < e21 < e55 < e200:   score += 4; reasons.append("EMA perfect bear")
        elif cl < e21 < e55 < e200:       score += 3; reasons.append("EMA full bear")
        elif cl < e21 < e55:              score += 2; reasons.append("EMA 21<55")
        elif cl < e21:                    score += 1; reasons.append("Below EMA21")
        if prev_cl > e8 and cl < e8:      score += 1; reasons.append("EMA8 cross ↓")

    # ── 5. Ichimoku ── max 2 ───────────────────────────────────────────
    tk  = float(c.get('ICH_TENKAN', cl) or cl)
    kj  = float(c.get('ICH_KIJUN',  cl) or cl)
    sa  = float(c.get('ICH_SPAN_A', cl) or cl)
    sb  = float(c.get('ICH_SPAN_B', cl) or cl)
    ptk = float(prev.get('ICH_TENKAN', tk) or tk)
    pkj = float(prev.get('ICH_KIJUN',  kj) or kj)
    cloud_top = max(sa, sb); cloud_bot = min(sa, sb)
    if direction == "LONG":
        if ptk < pkj and tk > kj: score += 2; reasons.append("Ichi TK cross ↑")
        if cl > cloud_top:        score += 1; reasons.append("Ichi above cloud")
    else:
        if ptk > pkj and tk < kj: score += 2; reasons.append("Ichi TK cross ↓")
        if cl < cloud_bot:        score += 1; reasons.append("Ichi below cloud")

    # ── 6. RSI ── max 4 ────────────────────────────────────────────────
    rsi      = float(c['RSI'])
    rsi_prev = float(prev.get('RSI', rsi) or rsi)
    if direction == "LONG":
        if rsi > 80: return 0, [f"RSI {rsi:.0f} OB"]
        if 40 <= rsi <= 65:   score += 2; reasons.append(f"RSI {rsi:.0f} ideal")
        elif 30 <= rsi < 40:  score += 2; reasons.append(f"RSI {rsi:.0f} OS")
        elif rsi < 30:        score += 1; reasons.append(f"RSI {rsi:.0f} deep OS")
        # Bullish divergence: RSI rising while price flat/down
        if rsi > rsi_prev and cl <= float(prev['close']) + float(prev.get('ATR', cl * 0.01) or cl * 0.01) * 0.3:
            score += 2; reasons.append("RSI bull div")
    else:
        if rsi < 20: return 0, [f"RSI {rsi:.0f} OS"]
        if 35 <= rsi <= 60:   score += 2; reasons.append(f"RSI {rsi:.0f} ideal")
        elif 60 < rsi <= 70:  score += 2; reasons.append(f"RSI {rsi:.0f} OB")
        elif rsi > 70:        score += 1; reasons.append(f"RSI {rsi:.0f} deep OB")
        if rsi < rsi_prev and cl >= float(prev['close']) - float(prev.get('ATR', cl * 0.01) or cl * 0.01) * 0.3:
            score += 2; reasons.append("RSI bear div")

    # ── 7. MACD ── max 3 ───────────────────────────────────────────────
    macd  = float(c.get('MACD',      0) or 0); macds  = float(c.get('MACDS',  0) or 0)
    pmacd = float(prev.get('MACD',   0) or 0); pmacds = float(prev.get('MACDS',0) or 0)
    hist  = float(c.get('MACD_HIST', 0) or 0); phist  = float(prev.get('MACD_HIST', 0) or 0)
    if direction == "LONG":
        if macd > macds and pmacd <= pmacds: score += 2; reasons.append("MACD cross ↑")
        elif macd > macds:                   score += 1; reasons.append("MACD bull")
        if hist > 0 and hist > phist:        score += 1; reasons.append("MACD hist ↑")
    else:
        if macd < macds and pmacd >= pmacds: score += 2; reasons.append("MACD cross ↓")
        elif macd < macds:                   score += 1; reasons.append("MACD bear")
        if hist < 0 and hist < phist:        score += 1; reasons.append("MACD hist ↓")

    # ── 8. Bollinger Bands ── max 3 ────────────────────────────────────
    bb_up  = float(c.get('BB_UP',  cl) or cl)
    bb_lo  = float(c.get('BB_LO',  cl) or cl)
    bb_ma  = float(c.get('BB_MA',  cl) or cl)
    bbw    = float(c.get('BB_WIDTH', 0) or 0)
    if bbw < 0.012: return 0, ["BB squeeze"]
    if direction == "LONG":
        if float(prev['close']) <= bb_lo and cl > bb_lo: score += 2; reasons.append("BB lower bounce")
        elif cl < bb_ma:                                  score += 1; reasons.append("BB lower half")
        if bbw > 0.04:                                    score += 1; reasons.append("BB expanding")
    else:
        if float(prev['close']) >= bb_up and cl < bb_up: score += 2; reasons.append("BB upper bounce")
        elif cl > bb_ma:                                  score += 1; reasons.append("BB upper half")
        if bbw > 0.04:                                    score += 1; reasons.append("BB expanding")

    # ── 9. VWAP ── max 2 ───────────────────────────────────────────────
    vwap = float(c.get('VWAP', cl) or cl)
    gap  = abs(cl - vwap) / (vwap + 1e-9)
    if direction == "LONG":
        if cl > vwap:
            score += 1; reasons.append("Above VWAP")
            if gap > 0.005: score += 1; reasons.append("VWAP gap >0.5%")
    else:
        if cl < vwap:
            score += 1; reasons.append("Below VWAP")
            if gap > 0.005: score += 1; reasons.append("VWAP gap >0.5%")

    # ── 10. Volume ── max 3 ────────────────────────────────────────────
    vm = float(c.get('VOL_MA20', 0) or 0)
    vr = c['volume'] / vm if vm > 0 else 1.0
    if vr > 2.0:   score += 3; reasons.append(f"Vol {vr:.1f}x")
    elif vr > 1.5: score += 2; reasons.append(f"Vol {vr:.1f}x")
    elif vr > 1.2: score += 1; reasons.append(f"Vol {vr:.1f}x")

    # ── 11. S/R levels ── max 3 ────────────────────────────────────────
    sr_at   = int(c.get('SR_AT',   0) or 0)
    sr_near = int(c.get('SR_NEAR', 0) or 0)
    if sr_at:        score += 3; reasons.append("At S/R level")
    elif sr_near:    score += 2; reasons.append("Near S/R level")
    else:
        # close but not swing-level: within 1%
        lb_s = max(0, i - 20)
        recent_highs = list(df['high'].iloc[lb_s:i])
        recent_lows  = list(df['low'].iloc[lb_s:i])
        all_sr = recent_highs + recent_lows
        all_sr = [x for x in all_sr if not pd.isna(x)]
        if all_sr:
            close_sr = min(abs(cl - lv) / (cl + 1e-9) for lv in all_sr)
            if close_sr <= 0.01: score += 1; reasons.append("Close to S/R")

    # ── 12. Candle patterns ── max 3 ───────────────────────────────────
    stoch = float(c.get('STOCHRSI', 0.5) or 0.5)
    if direction == "LONG"  and stoch > 0.92: return 0, ["StochRSI OB"]
    if direction == "SHORT" and stoch < 0.08: return 0, ["StochRSI OS"]
    if direction == "LONG":
        if int(c.get('BULL_ENGULF', 0)):     score += 3; reasons.append("Bull engulf ✓")
        elif int(c.get('HAMMER', 0)):         score += 2; reasons.append("Hammer ✓")
        elif int(c.get('SOLDIERS', 0)):       score += 2; reasons.append("3 soldiers ✓")
        elif int(c.get('INSIDE_BAR', 0)):     score += 1; reasons.append("Inside bar")
    else:
        if int(c.get('BEAR_ENGULF', 0)):     score += 3; reasons.append("Bear engulf ✓")
        elif int(c.get('SHOOT_STAR', 0)):     score += 2; reasons.append("Shoot star ✓")
        elif int(c.get('CROWS', 0)):          score += 2; reasons.append("3 crows ✓")
        elif int(c.get('INSIDE_BAR', 0)):     score += 1; reasons.append("Inside bar")

    # ── 13. Market structure ── max 1 ──────────────────────────────────
    if direction == "LONG"  and int(c.get('MS_BULL', 0)): score += 1; reasons.append("Mkt struct HH+HL")
    if direction == "SHORT" and int(c.get('MS_BEAR', 0)): score += 1; reasons.append("Mkt struct LH+LL")

    return max(score, 0), reasons

def calculate_levels(df, i, direction, entry):
    c   = df.iloc[i]
    atr = float(c['ATR']) if not pd.isna(c['ATR']) else entry * 0.015
    lb  = max(0, i - 20)
    rl  = df['low'].iloc[lb:i]; rh = df['high'].iloc[lb:i]
    if direction == "LONG":
        atr_sl = entry - atr * 1.8
        sw_sl  = float(rl.min()) * 0.998 if len(rl) > 0 else atr_sl
        sl  = min(atr_sl, sw_sl)
        sl  = min(sl, entry * 0.985); sl = max(sl, entry * 0.970)
        risk = entry - sl
        tp1 = entry + risk * 2.0; tp2 = entry + risk * 3.5
    else:
        atr_sl = entry + atr * 1.8
        sw_sl  = float(rh.max()) * 1.002 if len(rh) > 0 else atr_sl
        sl  = max(atr_sl, sw_sl)
        sl  = max(sl, entry * 1.015); sl = min(sl, entry * 1.030)
        risk = sl - entry
        tp1 = entry - risk * 2.0; tp2 = entry - risk * 3.5
    sl_pct  = abs(entry - sl) / entry * 100
    tp1_pct = abs(tp1 - entry) / entry * 100
    rr = tp1_pct / sl_pct if sl_pct > 0 else 0
    return sl, tp1, tp2, sl_pct, tp1_pct, rr

# ═══════════════════════════════════════════════
# PLACE FUTURES ORDER — FIXED (no workingType)
# ═══════════════════════════════════════════════
# ═══════════════════════════════════════════════
# PLACE FUTURES ORDER
# SL  → STOP        via /fapi/v1/order/algo  closePosition=true  (Binance handles)
# TP1 → TAKE_PROFIT via /fapi/v1/order/algo  closePosition=true  (Binance closes full @ TP1)
# TP2 → bot monitor ලා track කරලා market close  (software level)
# NOTE: reduceOnly testnet ලා reject කරනවා — use නොකරන්නේ
# ═══════════════════════════════════════════════
def place_futures_order(symbol, side, usdt_amount, entry_price, sl, tp1, tp2=None):
    state = load_state()
    set_leverage(symbol, LEVERAGE)

    info = get_exchange_info(symbol)
    if not info:
        add_error(state, f"{symbol}: exchange info failed")
        save_state(state)
        return None, "exchange_info_fail"

    notional = usdt_amount * LEVERAGE
    raw_qty  = notional / entry_price
    qty      = round_step(raw_qty, info["stepSize"])

    if qty * entry_price < info.get("minNotional", 5):
        add_error(state, f"{symbol}: qty too small ({qty} @ {entry_price:.4f})")
        save_state(state)
        return None, "qty_too_small"

    close_side = "SELL" if side == "BUY" else "BUY"

    # ── 1. Market entry ──
    entry_resp = fapi_post("/fapi/v1/order", {
        "symbol":       symbol,
        "side":         side,
        "type":         "MARKET",
        "quantity":     qty,
        "positionSide": "BOTH",
    })
    if not isinstance(entry_resp, dict) or "orderId" not in entry_resp:
        err = str(entry_resp)
        add_error(state, f"{symbol} entry failed: {err}")
        save_state(state)
        tg_error(f"{symbol} entry failed: {err[:200]}")
        return None, err

    order_id = str(entry_resp["orderId"])
    log.info(f"Entry: {symbol} {side} qty={qty} id={order_id}")
    time.sleep(0.5)

    sl_price  = round_step(sl,  info["tickSize"])
    tp1_price = round_step(tp1, info["tickSize"])

    # ── 2. Stop-Loss → algo endpoint (STOP_MARKET requires /fapi/v1/order/algo) ──
    sl_resp = fapi_post_algo({
        "symbol":        symbol,
        "side":          close_side,
        "type":          "STOP_MARKET",
        "triggerPrice":     sl_price,
        "closePosition": "true",
        "positionSide":  "BOTH",
        "timeInForce":   "GTC",
    })
    sl_order_id = ""
    if isinstance(sl_resp, dict) and "algoId" in sl_resp:
        sl_order_id = str(sl_resp["algoId"])
        log.info(f"SL placed: {symbol} @ {sl_price} id={sl_order_id}")
    else:
        add_error(state, f"{symbol} SL failed: {sl_resp}")
        save_state(state)

    time.sleep(0.3)

    # ── 3. TP1 → algo endpoint (TAKE_PROFIT_MARKET requires /fapi/v1/order/algo) ──
    #    TP2 is tracked by bot monitor in software — when price reaches tp2
    #    after tp1 would have closed, the bot records it as TP2_HIT via AUTO_CLOSED.
    #    If you want split TP, enable OCO/bracket orders on live (not testnet).
    tp1_resp = fapi_post_algo({
        "symbol":        symbol,
        "side":          close_side,
        "type":          "TAKE_PROFIT_MARKET",
        "triggerPrice":     tp1_price,
        "closePosition": "true",
        "positionSide":  "BOTH",
        "timeInForce":   "GTC",
    })
    tp1_order_id = ""
    if isinstance(tp1_resp, dict) and "algoId" in tp1_resp:
        tp1_order_id = str(tp1_resp["algoId"])
        log.info(f"TP1 placed: {symbol} @ {tp1_price} id={tp1_order_id}")
    else:
        add_error(state, f"{symbol} TP1 failed: {tp1_resp}")
        save_state(state)

    return {
        "order_id":     order_id,
        "sl_order_id":  sl_order_id,
        "tp_order_id":  tp1_order_id,
        "tp2_order_id": "",          # TP2 = software monitor only
        "qty":          qty,
        "half_qty":     qty,
        "notional":     round(notional, 2),
    }, "ok"

def cancel_orders(symbol, order_ids):
    """Cancel a list of order IDs. SL/TP orders are algo orders (algoId); use fapi_delete_algo for them."""
    for oid in order_ids:
        if not oid:
            continue
        # Algo orders (SL/TP) are cancelled via DELETE /fapi/v1/algoOrder with algoId.
        # Regular orders (entry/close) are cancelled via DELETE /fapi/v1/order with orderId.
        # Since 2025-12-09 all SL/TP IDs stored are algoIds — always use algo cancel.
        resp = fapi_delete_algo(oid)
        if isinstance(resp, dict) and resp.get("code") not in (None, "200", 200):
            # Fallback: try regular order cancel (for any legacy orderId still in state)
            fapi_delete("/fapi/v1/order", {"symbol": symbol, "orderId": oid})
        log.info(f"Cancelled algo order {oid} on {symbol}: {resp}")

def update_sl_tp_orders(trade, new_sl, new_tp1, info, new_tp2=None):
    symbol     = trade["symbol"]
    direction  = trade["direction"]
    close_side = "SELL" if direction == "LONG" else "BUY"

    # Cancel existing SL, TP1, TP2 orders
    cancel_orders(symbol, [
        trade.get("sl_order_id"),
        trade.get("tp_order_id"),
        trade.get("tp2_order_id"),
    ])
    time.sleep(0.3)

    sl_price  = round_step(new_sl,  info["tickSize"])
    tp1_price = round_step(new_tp1, info["tickSize"])

    sl_resp = fapi_post_algo({
        "symbol":        symbol,
        "side":          close_side,
        "type":          "STOP_MARKET",
        "triggerPrice":     sl_price,
        "closePosition": "true",
        "positionSide":  "BOTH",
        "timeInForce":   "GTC",
    })
    new_sl_id = str(sl_resp.get("algoId", "")) if isinstance(sl_resp, dict) and "algoId" in sl_resp else ""

    time.sleep(0.2)

    # TP1 — algo endpoint (no reduceOnly on testnet)
    tp1_resp = fapi_post_algo({
        "symbol":        symbol,
        "side":          close_side,
        "type":          "TAKE_PROFIT_MARKET",
        "triggerPrice":     tp1_price,
        "closePosition": "true",
        "positionSide":  "BOTH",
        "timeInForce":   "GTC",
    })
    new_tp1_id = str(tp1_resp.get("algoId", "")) if isinstance(tp1_resp, dict) and "algoId" in tp1_resp else ""

    return new_sl_id, new_tp1_id, ""  # tp2 = software monitor only

# ═══════════════════════════════════════════════
# MANUAL CLOSE (from dashboard)
# ═══════════════════════════════════════════════
def close_trade_market(trade):
    """Market close — cancel SL/TP first, then close position."""
    symbol    = trade["symbol"]
    direction = trade["direction"]
    qty       = trade.get("qty", 0)
    close_side = "SELL" if direction == "LONG" else "BUY"

    # Cancel existing SL/TP
    cancel_orders(symbol, [
        trade.get("sl_order_id"),
        trade.get("tp_order_id"),
        trade.get("tp2_order_id"),
    ])
    time.sleep(0.3)

    if qty <= 0:
        # Try to get qty from Binance position
        positions = get_binance_positions()
        for p in positions:
            if p["symbol"] == symbol:
                amt = abs(float(p.get("positionAmt", 0)))
                if amt > 0:
                    qty = amt
                    break

    if qty <= 0:
        return False, "qty_zero"

    # Close with market order — use closePosition=true instead of reduceOnly
    resp = fapi_post("/fapi/v1/order", {
        "symbol":        symbol,
        "side":          close_side,
        "type":          "MARKET",
        "quantity":      qty,
        "positionSide":  "BOTH",
    })
    if isinstance(resp, dict) and "orderId" in resp:
        return True, str(resp["orderId"])
    return False, str(resp)

# ═══════════════════════════════════════════════
# MONITOR OPEN TRADES
# ═══════════════════════════════════════════════
def monitor_open_trades():
    with _lock:
        state = load_state()

    # Sync with Binance positions first
    state = sync_positions_with_binance(state)
    save_state(state)

    if not state["open_trades"]:
        return

    closed_ids = []
    for trade in list(state["open_trades"]):
        sym   = trade["symbol"]
        price = get_price(sym)
        if price is None:
            continue

        direction = trade["direction"]
        entry = trade["entry"]
        sl    = trade["sl"]
        tp1   = trade["tp1"]
        tp2   = trade["tp2"]

        # SL/TP නෑ නම් (sync trade orders fail වෙලා ඇති) → retry place
        if sl == 0 or tp1 == 0:
            if direction == "LONG":
                live_pct = (price - entry) / entry * 100 if entry > 0 else 0
            else:
                live_pct = (entry - price) / entry * 100 if entry > 0 else 0
            log.info(f"MONITOR {sym} {direction} (NO SL/TP) price={price:.4f} pnl={live_pct:+.2f}% — retrying SL/TP placement")

            # Retry: calculate levels and place orders
            try:
                df_raw = get_klines(sym, "1h", DATA_LIMIT)
                if df_raw is not None and len(df_raw) >= 50:
                    df_ind = compute_indicators(df_raw)
                    i_idx  = len(df_ind) - 1
                    new_sl, new_tp1, new_tp2, sl_pct_, tp1_pct_, rr_ = \
                        calculate_levels(df_ind, i_idx, direction, entry)
                    info = get_exchange_info(sym)
                    if info and new_sl > 0:
                        qty_t      = trade.get("qty", 0)
                        half_qty_t = round_step(qty_t * 0.5, info["stepSize"]) if qty_t > 0 else 0
                        close_side = "SELL" if direction == "LONG" else "BUY"

                        # Cancel any stale orders first
                        cancel_orders(sym, [
                            trade.get("sl_order_id"),
                            trade.get("tp_order_id"),
                            trade.get("tp2_order_id"),
                        ])
                        time.sleep(0.3)

                        sl_r = fapi_post_algo({
                            "symbol":        sym,
                            "side":          close_side,
                            "type":          "STOP_MARKET",
                            "triggerPrice":     round_step(new_sl, info["tickSize"]),
                            "closePosition": "true",
                            "positionSide":  "BOTH",
                            "timeInForce":   "GTC",
                        })
                        new_sl_id = str(sl_r["algoId"]) if isinstance(sl_r, dict) and "algoId" in sl_r else ""
                        time.sleep(0.3)

                        # TP1 — algo endpoint (no reduceOnly on testnet)
                        tp1_r = fapi_post_algo({
                            "symbol":        sym,
                            "side":          close_side,
                            "type":          "TAKE_PROFIT_MARKET",
                            "triggerPrice":     round_step(new_tp1, info["tickSize"]),
                            "closePosition": "true",
                            "positionSide":  "BOTH",
                            "timeInForce":   "GTC",
                        })
                        new_tp1_id = str(tp1_r["algoId"]) if isinstance(tp1_r, dict) and "algoId" in tp1_r else ""
                        new_tp2_id = ""  # TP2 = software monitor only

                        # Update trade state
                        with _lock:
                            state2 = load_state()
                            for t in state2["open_trades"]:
                                if t["id"] == trade["id"]:
                                    t["sl"]  = round(new_sl,  6)
                                    t["tp1"] = round(new_tp1, 6)
                                    t["tp2"] = round(new_tp2, 6)
                                    t["sl_pct"]       = round(sl_pct_,  3)
                                    t["tp1_pct"]      = round(tp1_pct_, 3)
                                    t["rr"]           = round(rr_,      2)
                                    t["half_qty"]     = qty_t
                                    t["sl_order_id"]  = new_sl_id
                                    t["tp_order_id"]  = new_tp1_id
                                    t["tp2_order_id"] = ""
                                    break
                            add_log(state2, f"🔧 RETRY SL/TP {sym} {direction} | SL={new_sl:.4f} TP1={new_tp1:.4f} TP2={new_tp2:.4f}")
                            save_state(state2)
            except Exception as e:
                add_error(state, f"RETRY SL/TP error {sym}: {e}")
                save_state(state)
            continue

        if direction == "LONG":
            live_pct = (price - entry) / entry * 100
        else:
            live_pct = (entry - price) / entry * 100
        log.info(f"MONITOR {sym} {direction} price={price:.4f} pnl={live_pct:+.2f}% sl={sl:.4f} tp1={tp1:.4f}")

        outcome = None; pnl_pct = 0.0

        if direction == "LONG":
            if not trade.get("tp1_hit") and price >= tp1:
                trade["tp1_hit"] = True
                info = get_exchange_info(sym)
                if info:
                    # ── Move SL to breakeven ──
                    if trade.get("sl_order_id"):
                        cancel_orders(sym, [trade.get("sl_order_id")])
                        time.sleep(0.2)
                    be_price = round_step(entry, info["tickSize"])
                    sl_resp = fapi_post_algo({
                        "symbol":        sym,
                        "side":          "SELL",
                        "type":          "STOP_MARKET",
                        "triggerPrice":  be_price,
                        "closePosition": "true",
                        "positionSide":  "BOTH",
                        "timeInForce":   "GTC",
                    })
                    if isinstance(sl_resp, dict) and "algoId" in sl_resp:
                        trade["sl_order_id"] = str(sl_resp["algoId"])
                        trade["sl"] = entry
                    # ── Cancel old TP1 algo order if still open, then place TP2 ──
                    if trade.get("tp_order_id"):
                        cancel_orders(sym, [trade.get("tp_order_id")])
                        time.sleep(0.2)
                    tp2_price = round_step(tp2, info["tickSize"])
                    tp2_resp = fapi_post_algo({
                        "symbol":        sym,
                        "side":          "SELL",
                        "type":          "TAKE_PROFIT_MARKET",
                        "triggerPrice":  tp2_price,
                        "closePosition": "true",
                        "positionSide":  "BOTH",
                        "timeInForce":   "GTC",
                    })
                    if isinstance(tp2_resp, dict) and "algoId" in tp2_resp:
                        trade["tp2_order_id"] = str(tp2_resp["algoId"])
                        trade["tp_order_id"]  = ""
                        add_log(load_state(), f"🎯 {sym} TP1 hit → TP2 order placed @ ${tp2_price:.4f}")
                    else:
                        add_log(load_state(), f"⚠ {sym} TP2 order failed: {tp2_resp}")
                tg(f"🎯 *{sym} LONG TP1 hit* @ `${tp1:.4f}`\n"
                   f"SL → breakeven `${entry:.4f}` | TP2 order → `${tp2:.4f}`")
            if trade.get("tp1_hit") and price >= tp2:
                outcome = "TP2_HIT"
                pnl_pct = ((tp1-entry)/entry*100)*0.5 + ((tp2-entry)/entry*100)*0.5
            elif price <= trade["sl"]:
                outcome = "BREAKEVEN" if trade.get("tp1_hit") else "SL_HIT"
                pnl_pct = (tp1-entry)/entry*100*0.5 if trade.get("tp1_hit") else (trade["sl"]-entry)/entry*100
        else:
            if not trade.get("tp1_hit") and price <= tp1:
                trade["tp1_hit"] = True
                info = get_exchange_info(sym)
                if info:
                    # ── Move SL to breakeven ──
                    if trade.get("sl_order_id"):
                        cancel_orders(sym, [trade.get("sl_order_id")])
                        time.sleep(0.2)
                    be_price = round_step(entry, info["tickSize"])
                    sl_resp = fapi_post_algo({
                        "symbol":        sym,
                        "side":          "BUY",
                        "type":          "STOP_MARKET",
                        "triggerPrice":  be_price,
                        "closePosition": "true",
                        "positionSide":  "BOTH",
                        "timeInForce":   "GTC",
                    })
                    if isinstance(sl_resp, dict) and "algoId" in sl_resp:
                        trade["sl_order_id"] = str(sl_resp["algoId"])
                        trade["sl"] = entry
                    # ── Cancel old TP1 algo order if still open, then place TP2 ──
                    if trade.get("tp_order_id"):
                        cancel_orders(sym, [trade.get("tp_order_id")])
                        time.sleep(0.2)
                    tp2_price = round_step(tp2, info["tickSize"])
                    tp2_resp = fapi_post_algo({
                        "symbol":        sym,
                        "side":          "BUY",
                        "type":          "TAKE_PROFIT_MARKET",
                        "triggerPrice":  tp2_price,
                        "closePosition": "true",
                        "positionSide":  "BOTH",
                        "timeInForce":   "GTC",
                    })
                    if isinstance(tp2_resp, dict) and "algoId" in tp2_resp:
                        trade["tp2_order_id"] = str(tp2_resp["algoId"])
                        trade["tp_order_id"]  = ""
                        add_log(load_state(), f"🎯 {sym} TP1 hit → TP2 order placed @ ${tp2_price:.4f}")
                    else:
                        add_log(load_state(), f"⚠ {sym} TP2 order failed: {tp2_resp}")
                tg(f"🎯 *{sym} SHORT TP1 hit* @ `${tp1:.4f}`\n"
                   f"SL → breakeven `${entry:.4f}` | TP2 order → `${tp2:.4f}`")
            if trade.get("tp1_hit") and price <= tp2:
                outcome = "TP2_HIT"
                pnl_pct = ((entry-tp1)/entry*100)*0.5 + ((entry-tp2)/entry*100)*0.5
            elif price >= trade["sl"]:
                outcome = "BREAKEVEN" if trade.get("tp1_hit") else "SL_HIT"
                pnl_pct = (entry-tp1)/entry*100*0.5 if trade.get("tp1_hit") else (entry-trade["sl"])/entry*100

        if outcome:
            alloc    = trade["alloc_usd"]
            notional = trade.get("notional", alloc * LEVERAGE)
            pnl_usd  = notional * abs(pnl_pct) / 100
            if outcome == "SL_HIT":
                pnl_usd = -pnl_usd

            cancel_orders(sym, [trade.get("sl_order_id"), trade.get("tp_order_id"), trade.get("tp2_order_id")])

            with _lock:
                state = load_state()
                state["total_pnl"] = round(state.get("total_pnl", 0) + pnl_usd, 2)
                if outcome == "TP2_HIT":   state["wins"]   += 1
                elif outcome == "BREAKEVEN": state["bes"]  += 1
                else:                        state["losses"] += 1

                trade["outcome"]    = outcome
                trade["pnl_usd"]    = round(pnl_usd, 2)
                trade["pnl_pct"]    = round(pnl_pct, 3)
                trade["close_time"] = dt_sl()
                trade["exit_price"] = price
                state["closed_trades"].insert(0, trade)
                state["closed_trades"] = state["closed_trades"][:500]
                closed_ids.append(trade["id"])

                em = "✅" if outcome == "TP2_HIT" else "🟡" if outcome == "BREAKEVEN" else "❌"
                add_log(state, f"{em} {sym} {direction} → {outcome} | P&L: ${pnl_usd:+.2f}")
                save_state(state)

            tg(f"{em} *{sym} {direction} — {outcome}*\n"
               f"Entry `${entry:.4f}` → Exit `${price:.4f}`\n"
               f"P&L: *${pnl_usd:+.2f}* ({pnl_pct:+.2f}%) `x{LEVERAGE}`\n"
               f"Alloc: `${alloc:.2f}` | Notional: `${notional:.2f}`")

    if closed_ids:
        with _lock:
            state = load_state()
            state["open_trades"] = [t for t in state["open_trades"] if t["id"] not in closed_ids]
            save_state(state)

# ═══════════════════════════════════════════════
# MAIN SCAN
# ═══════════════════════════════════════════════
def scan_once():
    with _lock:
        state = load_state()

    if not state.get("bot_running", True):
        add_log(state, "⏸ Bot paused — skip scan")
        save_state(state); return

    today = today_sl()
    if state.get("signals_date") != today:
        state["signals_today"] = 0
        state["signals_date"]  = today

    if len(state["open_trades"]) >= MAX_OPEN_TRADES:
        add_log(state, f"⛔ Max {MAX_OPEN_TRADES} trades open — skip scan")
        save_state(state); return

    if state.get("signals_today", 0) >= MAX_SIGNALS_DAY:
        add_log(state, f"📊 Daily limit {MAX_SIGNALS_DAY} reached")
        save_state(state); return

    avail_bal, wallet_bal = get_wallet_balance()
    if avail_bal is not None:
        state["balance"]        = round(avail_bal, 2)
        state["wallet_balance"] = round(wallet_bal, 2)
    else:
        add_error(state, "Could not fetch wallet balance")

    state["pending_orders_count"] = get_all_open_orders_count()
    add_log(state, f"🔍 Scanning {len(WATCH_LIST)} coins | Balance: ${state['balance']:.2f}")
    state["bot_status"] = "scanning"
    state["last_scan"]  = ts_sl()
    save_state(state)

    found = 0; scan_results = []

    for coin in WATCH_LIST:
        if len(state["open_trades"]) >= MAX_OPEN_TRADES: break
        if state.get("signals_today", 0) >= MAX_SIGNALS_DAY: break

        htf_trend, htf_strength = get_htf_trend(coin)
        df_raw = get_klines(coin, "1h", DATA_LIMIT)
        if df_raw is None:
            scan_results.append(f"{coin}:klines_fail")
            continue

        df = compute_indicators(df_raw)
        i  = len(df) - 1
        if i < 100:
            scan_results.append(f"{coin}:data_short"); continue

        c = df.iloc[i]
        if any(pd.isna(c.get(k, float('nan'))) for k in ['ATR','EMA200','STOCHRSI','ADX','VWAP','ICH_KIJUN']):
            scan_results.append(f"{coin}:indicator_nan"); continue

        if htf_trend == "BULL":       directions = ["LONG"]
        elif htf_trend == "BEAR":     directions = ["SHORT"]
        else:
            st_now = int(c.get('ST_DIR', 1) or 1)
            directions = ["LONG"] if st_now == 1 else ["SHORT"]

        for direction in directions:
            score, reasons = score_signal(df, i, direction, htf_trend, htf_strength)
            if score < MIN_SCORE:
                scan_results.append(f"{coin}:{direction}_sc{score}"); continue

            entry = float(c['close'])
            sl, tp1, tp2, sl_pct, tp1_pct, rr = calculate_levels(df, i, direction, entry)
            if rr < MIN_RR:
                scan_results.append(f"{coin}:{direction}_rr{rr:.1f}"); continue

            with _lock:
                state = load_state()

            # ── Duplicate signal check ──
            existing_same = [t for t in state["open_trades"]
                             if t["symbol"] == coin and t["direction"] == direction]
            if existing_same:
                old_trade = existing_same[0]
                old_score = old_trade.get("score", 0)
                if score > old_score + 2:
                    info = get_exchange_info(coin)
                    if info:
                        new_sl_id, new_tp_id, new_tp2_id = update_sl_tp_orders(old_trade, sl, tp1, info, tp2)
                        with _lock:
                            state = load_state()
                            for t in state["open_trades"]:
                                if t["id"] == old_trade["id"]:
                                    t["sl"] = round(sl, 6); t["tp1"] = round(tp1, 6)
                                    t["tp2"] = round(tp2, 6); t["score"] = score
                                    t["sl_order_id"] = new_sl_id; t["tp_order_id"] = new_tp_id
                                    t["tp2_order_id"] = new_tp2_id
                                    t["reasons"] = reasons[:6]
                                    break
                            add_log(state, f"🔄 {coin} {direction} updated score {old_score}→{score}")
                            save_state(state)
                    scan_results.append(f"{coin}:{direction}_UPDATED")
                else:
                    scan_results.append(f"{coin}:{direction}_dup_skip")
                continue

            # ── New trade ──
            alloc_pct = SCORE_ALLOC_PCT.get(min(score, 11), 2.0)
            alloc_usd = round(state["balance"] * (alloc_pct / 100.0), 2)
            alloc_usd = min(alloc_usd, state["balance"] * MAX_ALLOC_PCT)
            alloc_usd = round(alloc_usd, 2)

            if alloc_usd < MIN_TRADE_USDT:
                scan_results.append(f"{coin}:alloc_low${alloc_usd:.0f}"); continue

            side = "BUY" if direction == "LONG" else "SELL"
            order_info, status = place_futures_order(coin, side, alloc_usd, entry, sl, tp1, tp2)
            if order_info is None:
                scan_results.append(f"{coin}:order_fail_{status[:20]}"); continue

            trade = {
                "id":           f"{coin}_{direction}_{int(time.time())}",
                "symbol":       coin,
                "direction":    direction,
                "score":        score,
                "entry":        entry,
                "sl":           round(sl, 6),
                "tp1":          round(tp1, 6),
                "tp2":          round(tp2, 6),
                "sl_pct":       round(sl_pct, 3),
                "tp1_pct":      round(tp1_pct, 3),
                "rr":           round(rr, 2),
                "alloc_usd":    alloc_usd,
                "alloc_pct":    alloc_pct,
                "notional":     order_info["notional"],
                "qty":          order_info["qty"],
                "half_qty":     order_info.get("half_qty", order_info["qty"]),
                "leverage":     LEVERAGE,
                "htf":          htf_trend,
                "reasons":      reasons[:6],
                "open_time":    dt_sl(),
                "order_id":     order_info["order_id"],
                "sl_order_id":  order_info["sl_order_id"],
                "tp_order_id":  order_info["tp_order_id"],
                "tp2_order_id": order_info.get("tp2_order_id", ""),
                "tp1_hit":      False,
                "outcome":      "OPEN",
            }

            with _lock:
                state = load_state()
                state["open_trades"].append(trade)
                state["signals_today"] = state.get("signals_today", 0) + 1
                add_log(state, f"{'📈' if direction=='LONG' else '📉'} {direction} {coin} | Score:{score} Alloc:${alloc_usd:.2f} | SL:{sl:.4f} TP:{tp1:.4f} | ✅ #{order_info['order_id']}")
                save_state(state)

            scan_results.append(f"{coin}:{direction}_TRADE_sc{score}")
            tg(f"{'📈' if direction=='LONG' else '📉'} *{direction} {coin}*\n"
               f"Score `{score}` | HTF `{htf_trend}` | RR `1:{rr:.1f}` | Lev `{LEVERAGE}x`\n"
               f"Entry `${entry:.4f}` | SL `${sl:.4f}`\n"
               f"TP1 `${tp1:.4f}` | TP2 `${tp2:.4f}`\n"
               f"Margin `${alloc_usd:.2f}` | Notional `${order_info['notional']:.0f}`\n"
               f"_{' | '.join(reasons[:4])}_")

            found += 1
            time.sleep(0.5)

    with _lock:
        state = load_state()
        state["bot_status"] = "idle" if state.get("bot_running", True) else "stopped"
        add_log(state, f"✅ Scan done — {found} signals | Open:{len(state['open_trades'])}")
        for i in range(0, len(scan_results), 4):
            add_log(state, "📋 " + " | ".join(scan_results[i:i+4]))
        save_state(state)

# ═══════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════
def run():
    log.info("🤖 Futures Bot starting")
    with _lock:
        state = load_state()
        add_log(state, "🚀 Bot started — Binance Futures Testnet")
        state["bot_status"]  = "running"
        state["bot_running"] = True
        save_state(state)

    tg("🚀 *Futures Bot Started*\n"
       f"Exchange: `testnet.binancefuture.com`\n"
       f"Leverage: `{LEVERAGE}x` | Max trades: `{MAX_OPEN_TRADES}`\n"
       f"Coins: `{len(WATCH_LIST)}`")

    scan_counter = 0
    while True:
        try:
            with _lock:
                state = load_state()
            bot_running = state.get("bot_running", True)

            if bot_running:
                monitor_open_trades()
                if scan_counter % 15 == 0:
                    scan_once()
            else:
                with _lock:
                    state = load_state()
                    state["bot_status"] = "stopped"
                    save_state(state)

            scan_counter += 1
            time.sleep(60)

        except KeyboardInterrupt:
            log.info("Bot stopped")
            break
        except Exception as e:
            err = str(e)
            log.error(f"Main loop error: {err}")
            with _lock:
                state = load_state()
                add_error(state, f"Main loop: {err}")
                save_state(state)
            tg_error(f"Main loop crash: {err[:200]}")
            time.sleep(30)

if __name__ == "__main__":
    run()