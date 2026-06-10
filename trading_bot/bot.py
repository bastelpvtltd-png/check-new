import time, os, json, threading, logging, hmac, hashlib
from datetime import datetime
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

FUTURES_BASE   = "https://testnet.binancefuture.com"   # Futures testnet
STATE_FILE     = os.environ.get("STATE_FILE", "/tmp/bot_state.json")

WATCH_LIST = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT",
    "ADAUSDT","LINKUSDT","AVAXUSDT","DOTUSDT","MATICUSDT",
    "LTCUSDT","UNIUSDT","NEARUSDT","APTUSDT","INJUSDT",
    "OPUSDT","ARBUSDT","SUIUSDT","TIAUSDT","FETUSDT"
]

MIN_SCORE        = 9
MAX_SIGNALS_DAY  = 15
MAX_OPEN_TRADES  = 15
MIN_RR           = 1.8
DATA_LIMIT       = 300
LEVERAGE         = 1
SCORE_ALLOC_PCT  = {4:2.0, 5:3.0, 6:5.0, 7:7.0, 8:9.0, 9:12.0, 10:15.0, 11:15.0}
MAX_ALLOC_PCT    = 0.15
MIN_TRADE_USDT   = 10.0

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
            "bot_running": True   # NEW: start/stop control
        }

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)

def add_log(state, msg, level="INFO"):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    state["log"].insert(0, {"ts": ts, "msg": msg, "level": level})
    state["log"] = state["log"][:300]
    log.info(msg)

def add_error(state, msg):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    state["errors"].insert(0, {"ts": ts, "msg": msg})
    state["errors"] = state["errors"][:50]
    add_log(state, f"❗ {msg}", "ERROR")

# ═══════════════════════════════════════════════
# TELEGRAM — trade alerts only, no signal spam
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
        data = r.json()
        return data
    except Exception as e:
        log.error(f"fapi POST {path} exception: {e}")
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
    """Get USDT balance from futures testnet account."""
    data = fapi_get("/fapi/v2/account", {}, signed=True)
    if data and "assets" in data:
        for a in data["assets"]:
            if a.get("asset") == "USDT":
                return float(a.get("availableBalance", 0)), float(a.get("walletBalance", 0))
    # fallback
    data2 = fapi_get("/fapi/v2/balance", {}, signed=True)
    if isinstance(data2, list):
        for b in data2:
            if b.get("asset") == "USDT":
                return float(b.get("availableBalance", 0)), float(b.get("balance", 0))
    return None, None

def get_open_orders_count(symbol=None):
    """Get count of pending/open orders from Binance."""
    params = {}
    if symbol:
        params["symbol"] = symbol
    data = fapi_get("/fapi/v1/openOrders", params, signed=True)
    if isinstance(data, list):
        return len(data)
    return 0

def get_all_open_orders_count():
    """Get total pending orders count across all symbols."""
    data = fapi_get("/fapi/v1/openOrders", {}, signed=True)
    if isinstance(data, list):
        return len(data)
    return 0

def get_klines(symbol, interval="1h", limit=300):
    data = fapi_get("/fapi/v1/klines", {
        "symbol": symbol, "interval": interval, "limit": limit
    })
    if not data or isinstance(data, dict):
        log.warning(f"klines {symbol} failed: {data}")
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
    """Get min qty, stepSize, tickSize for a symbol."""
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

# ═══════════════════════════════════════════════
# SET LEVERAGE
# ═══════════════════════════════════════════════
def set_leverage(symbol, leverage=LEVERAGE):
    resp = fapi_post("/fapi/v1/leverage", {
        "symbol": symbol, "leverage": leverage
    })
    if isinstance(resp, dict) and "leverage" in resp:
        return True
    log.warning(f"set_leverage {symbol} failed: {resp}")
    return False

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

    # SuperTrend
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
    return d

# ═══════════════════════════════════════════════
# SIGNAL SCORING
# ═══════════════════════════════════════════════
def score_signal(df, i, direction, htf_trend, htf_strength):
    c    = df.iloc[i]
    prev = df.iloc[i - 1]
    score = 0; reasons = []

    # HTF alignment
    if direction == "LONG" and htf_trend == "BULL":
        score += htf_strength; reasons.append(f"HTF BULL +{htf_strength}")
    elif direction == "SHORT" and htf_trend == "BEAR":
        score += htf_strength; reasons.append(f"HTF BEAR +{htf_strength}")
    elif htf_trend == "NEUTRAL":
        score += 1; reasons.append("HTF neutral")
    else:
        return 0, ["HTF counter-trend"]

    # ADX
    adx = float(c.get('ADX', 0) or 0)
    if adx < 18: return 0, [f"ADX {adx:.1f} flat"]
    if adx >= 35:   score += 3; reasons.append(f"ADX {adx:.1f} strong")
    elif adx >= 25: score += 2; reasons.append(f"ADX {adx:.1f} ok")
    else:           score += 1; reasons.append(f"ADX {adx:.1f}")

    # SuperTrend — soft
    st = int(c.get('ST_DIR', 0) or 0)
    if direction == "LONG":
        if st == 1:  score += 2; reasons.append("ST bull ✓")
        else:        score -= 1; reasons.append("ST bear ⚠")
    else:
        if st == -1: score += 2; reasons.append("ST bear ✓")
        else:        score -= 1; reasons.append("ST bull ⚠")

    # EMA stack
    cl   = float(c['close']); prev_cl = float(prev['close'])
    e8   = float(c['EMA8']);  e21 = float(c['EMA21'])
    e55  = float(c['EMA55']); e200 = float(c['EMA200'])
    if direction == "LONG":
        if cl > e8 > e21 > e55 > e200:   score += 4; reasons.append("EMA perfect bull")
        elif cl > e21 > e55 > e200:        score += 3; reasons.append("EMA full bull")
        elif cl > e21 > e55:               score += 2; reasons.append("EMA 21>55")
        elif cl > e21:                     score += 1; reasons.append("Above EMA21")
        if prev_cl < e8 and cl > e8:       score += 1; reasons.append("EMA8 cross ↑")
    else:
        if cl < e8 < e21 < e55 < e200:   score += 4; reasons.append("EMA perfect bear")
        elif cl < e21 < e55 < e200:        score += 3; reasons.append("EMA full bear")
        elif cl < e21 < e55:               score += 2; reasons.append("EMA 21<55")
        elif cl < e21:                     score += 1; reasons.append("Below EMA21")
        if prev_cl > e8 and cl < e8:       score += 1; reasons.append("EMA8 cross ↓")

    # RSI
    rsi = float(c['RSI'])
    if direction == "LONG":
        if rsi > 80: return 0, [f"RSI {rsi:.0f} OB"]
        if 40 <= rsi <= 65:  score += 2; reasons.append(f"RSI {rsi:.0f} ideal")
        elif 30 <= rsi < 40: score += 2; reasons.append(f"RSI {rsi:.0f} OS")
        elif rsi < 30:       score += 1; reasons.append(f"RSI {rsi:.0f} deep OS")
    else:
        if rsi < 20: return 0, [f"RSI {rsi:.0f} OS"]
        if 35 <= rsi <= 60:  score += 2; reasons.append(f"RSI {rsi:.0f} ideal")
        elif 60 < rsi <= 70: score += 2; reasons.append(f"RSI {rsi:.0f} OB")
        elif rsi > 70:       score += 1; reasons.append(f"RSI {rsi:.0f} deep OB")

    # MACD
    macd  = float(c.get('MACD', 0) or 0); macds  = float(c.get('MACDS', 0) or 0)
    pmacd = float(prev.get('MACD', 0) or 0); pmacds = float(prev.get('MACDS', 0) or 0)
    hist  = float(c.get('MACD_HIST', 0) or 0); phist = float(prev.get('MACD_HIST', 0) or 0)
    if direction == "LONG":
        if macd > macds and pmacd <= pmacds: score += 2; reasons.append("MACD cross ↑")
        elif macd > macds:                   score += 1; reasons.append("MACD bull")
        if hist > 0 and hist > phist:        score += 1; reasons.append("MACD hist ↑")
    else:
        if macd < macds and pmacd >= pmacds: score += 2; reasons.append("MACD cross ↓")
        elif macd < macds:                   score += 1; reasons.append("MACD bear")
        if hist < 0 and hist < phist:        score += 1; reasons.append("MACD hist ↓")

    # BB squeeze filter
    bbw = float(c.get('BB_WIDTH', 0) or 0)
    if bbw < 0.012: return 0, ["BB squeeze"]

    # Volume
    vm = float(c.get('VOL_MA20', 0) or 0)
    vr = c['volume'] / vm if vm > 0 else 1.0
    if vr > 2.0:   score += 3; reasons.append(f"Vol {vr:.1f}x")
    elif vr > 1.5: score += 2; reasons.append(f"Vol {vr:.1f}x")
    elif vr > 1.2: score += 1; reasons.append(f"Vol {vr:.1f}x")

    # StochRSI extremes
    stoch = float(c.get('STOCHRSI', 0.5) or 0.5)
    if direction == "LONG"  and stoch > 0.92: return 0, ["StochRSI OB"]
    if direction == "SHORT" and stoch < 0.08: return 0, ["StochRSI OS"]

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
# PLACE FUTURES ORDER — FIXED SL/TP
# ═══════════════════════════════════════════════
def place_futures_order(symbol, side, usdt_amount, entry_price, sl, tp1):
    """Place a futures market order with SL and TP on testnet."""
    state = load_state()

    # Set leverage first
    set_leverage(symbol, LEVERAGE)

    # Get exchange info for precision
    info = get_exchange_info(symbol)
    if not info:
        add_error(state, f"{symbol}: exchange info failed")
        save_state(state)
        return None, "exchange_info_fail"

    notional = usdt_amount * LEVERAGE
    raw_qty  = notional / entry_price
    qty      = round_step(raw_qty, info["stepSize"])

    if qty * entry_price < info.get("minNotional", 5):
        add_error(state, f"{symbol}: qty too small ({qty} @ {entry_price})")
        save_state(state)
        return None, "qty_too_small"

    # Determine closing side
    close_side = "SELL" if side == "BUY" else "BUY"

    # ── 1. Market entry order ──
    entry_resp = fapi_post("/fapi/v1/order", {
        "symbol":       symbol,
        "side":         side,
        "type":         "MARKET",
        "quantity":     qty,
        "positionSide": "BOTH",
    })

    if not isinstance(entry_resp, dict) or "orderId" not in entry_resp:
        err = str(entry_resp)
        add_error(state, f"{symbol} entry order failed: {err}")
        save_state(state)
        tg_error(f"{symbol} entry failed: {err[:200]}")
        return None, err

    order_id = str(entry_resp["orderId"])
    log.info(f"Entry order placed: {symbol} {side} qty={qty} orderId={order_id}")
    time.sleep(0.3)  # small wait so position registers before SL/TP

    # ── 2. Stop-Loss order (STOP_MARKET) ──
    sl_price = round_step(sl, info["tickSize"])
    sl_params = {
        "symbol":        symbol,
        "side":          close_side,
        "type":          "STOP_MARKET",
        "stopPrice":     sl_price,
        "closePosition": "true",
        "positionSide":  "BOTH",
        "timeInForce":   "GTC",
        "workingType":   "MARK_PRICE",   # use mark price to avoid false triggers
    }
    sl_resp = fapi_post("/fapi/v1/order", sl_params)
    sl_order_id = ""
    if isinstance(sl_resp, dict) and "orderId" in sl_resp:
        sl_order_id = str(sl_resp["orderId"])
        log.info(f"SL order placed: {symbol} stopPrice={sl_price} orderId={sl_order_id}")
    else:
        log.warning(f"SL order failed for {symbol}: {sl_resp}")
        add_error(state, f"{symbol} SL order failed: {sl_resp}")
        save_state(state)

    time.sleep(0.2)

    # ── 3. Take-Profit order (TAKE_PROFIT_MARKET) ──
    tp_price = round_step(tp1, info["tickSize"])
    tp_params = {
        "symbol":        symbol,
        "side":          close_side,
        "type":          "TAKE_PROFIT_MARKET",
        "stopPrice":     tp_price,
        "closePosition": "true",
        "positionSide":  "BOTH",
        "timeInForce":   "GTC",
        "workingType":   "MARK_PRICE",   # use mark price
    }
    tp_resp = fapi_post("/fapi/v1/order", tp_params)
    tp_order_id = ""
    if isinstance(tp_resp, dict) and "orderId" in tp_resp:
        tp_order_id = str(tp_resp["orderId"])
        log.info(f"TP order placed: {symbol} stopPrice={tp_price} orderId={tp_order_id}")
    else:
        log.warning(f"TP order failed for {symbol}: {tp_resp}")
        add_error(state, f"{symbol} TP order failed: {tp_resp}")
        save_state(state)

    return {
        "order_id":    order_id,
        "sl_order_id": sl_order_id,
        "tp_order_id": tp_order_id,
        "qty":         qty,
        "notional":    round(notional, 2),
    }, "ok"

def cancel_orders(symbol, order_ids):
    for oid in order_ids:
        if not oid:
            continue
        resp = fapi_delete("/fapi/v1/order", {"symbol": symbol, "orderId": oid})
        log.info(f"Cancelled order {oid} on {symbol}: {resp}")

def update_sl_tp_orders(trade, new_sl, new_tp1, info):
    """Cancel old SL/TP and place new ones with updated levels."""
    symbol = trade["symbol"]
    side   = trade["direction"]
    close_side = "SELL" if side == "LONG" else "BUY"

    # Cancel existing SL and TP
    cancel_orders(symbol, [trade.get("sl_order_id"), trade.get("tp_order_id")])
    time.sleep(0.3)

    # New SL
    sl_price = round_step(new_sl, info["tickSize"])
    sl_resp = fapi_post("/fapi/v1/order", {
        "symbol":        symbol,
        "side":          close_side,
        "type":          "STOP_MARKET",
        "stopPrice":     sl_price,
        "closePosition": "true",
        "positionSide":  "BOTH",
        "timeInForce":   "GTC",
        "workingType":   "MARK_PRICE",
    })
    new_sl_id = str(sl_resp.get("orderId", "")) if isinstance(sl_resp, dict) else ""

    time.sleep(0.2)

    # New TP
    tp_price = round_step(new_tp1, info["tickSize"])
    tp_resp = fapi_post("/fapi/v1/order", {
        "symbol":        symbol,
        "side":          close_side,
        "type":          "TAKE_PROFIT_MARKET",
        "stopPrice":     tp_price,
        "closePosition": "true",
        "positionSide":  "BOTH",
        "timeInForce":   "GTC",
        "workingType":   "MARK_PRICE",
    })
    new_tp_id = str(tp_resp.get("orderId", "")) if isinstance(tp_resp, dict) else ""

    return new_sl_id, new_tp_id

def close_trade_market(trade):
    """Force close a trade with a market order (for dashboard close button)."""
    symbol    = trade["symbol"]
    direction = trade["direction"]
    qty       = trade.get("qty", 0)
    close_side = "SELL" if direction == "LONG" else "BUY"

    # Cancel existing SL/TP first
    cancel_orders(symbol, [trade.get("sl_order_id"), trade.get("tp_order_id")])
    time.sleep(0.2)

    if qty <= 0:
        return False, "qty_zero"

    resp = fapi_post("/fapi/v1/order", {
        "symbol":       symbol,
        "side":         close_side,
        "type":         "MARKET",
        "quantity":     qty,
        "positionSide": "BOTH",
        "reduceOnly":   "true",
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

        # Live P&L
        if direction == "LONG":
            live_pct = (price - entry) / entry * 100
        else:
            live_pct = (entry - price) / entry * 100
        log.info(f"MONITOR {sym} {direction} price={price:.4f} pnl={live_pct:+.2f}% sl={sl:.4f} tp1={tp1:.4f}")

        outcome = None; pnl_pct = 0.0

        if direction == "LONG":
            if not trade.get("tp1_hit") and price >= tp1:
                trade["tp1_hit"] = True
                tg(f"🎯 *{sym} TP1 hit* — Moving SL to breakeven")
            if trade.get("tp1_hit") and price >= tp2:
                outcome = "TP2_HIT"
                pnl_pct = ((tp1-entry)/entry*100)*0.5 + ((tp2-entry)/entry*100)*0.5
            elif price <= sl:
                outcome = "BREAKEVEN" if trade.get("tp1_hit") else "SL_HIT"
                pnl_pct = (tp1-entry)/entry*100*0.5 if trade.get("tp1_hit") else (sl-entry)/entry*100
        else:
            if not trade.get("tp1_hit") and price <= tp1:
                trade["tp1_hit"] = True
                tg(f"🎯 *{sym} TP1 hit* — Moving SL to breakeven")
            if trade.get("tp1_hit") and price <= tp2:
                outcome = "TP2_HIT"
                pnl_pct = ((entry-tp1)/entry*100)*0.5 + ((entry-tp2)/entry*100)*0.5
            elif price >= sl:
                outcome = "BREAKEVEN" if trade.get("tp1_hit") else "SL_HIT"
                pnl_pct = (entry-tp1)/entry*100*0.5 if trade.get("tp1_hit") else (entry-sl)/entry*100

        if outcome:
            alloc    = trade["alloc_usd"]
            notional = trade.get("notional", alloc * LEVERAGE)
            if outcome == "TP2_HIT":
                pnl_usd = notional * abs(pnl_pct) / 100
            elif outcome == "SL_HIT":
                pnl_usd = -notional * abs(pnl_pct) / 100
            else:
                pnl_usd = notional * abs(pnl_pct) / 100

            # Cancel remaining orders on exchange
            cancel_orders(sym, [trade.get("sl_order_id"), trade.get("tp_order_id")])

            with _lock:
                state = load_state()
                state["total_pnl"] = round(state.get("total_pnl", 0) + pnl_usd, 2)
                if outcome == "TP2_HIT": state["wins"]   += 1
                elif outcome == "BREAKEVEN": state["bes"] += 1
                else: state["losses"] += 1

                trade["outcome"]    = outcome
                trade["pnl_usd"]    = round(pnl_usd, 2)
                trade["pnl_pct"]    = round(pnl_pct, 3)
                trade["close_time"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
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
# MAIN SCAN — with duplicate signal logic
# ═══════════════════════════════════════════════
def scan_once():
    with _lock:
        state = load_state()

    # If bot is stopped via dashboard, skip scan
    if not state.get("bot_running", True):
        add_log(state, "⏸ Bot is paused — skipping scan")
        save_state(state)
        return

    today = datetime.utcnow().strftime("%Y-%m-%d")
    if state.get("signals_date") != today:
        state["signals_today"] = 0
        state["signals_date"]  = today

    if len(state["open_trades"]) >= MAX_OPEN_TRADES:
        add_log(state, f"⛔ Max {MAX_OPEN_TRADES} trades open — skip scan")
        save_state(state); return

    if state.get("signals_today", 0) >= MAX_SIGNALS_DAY:
        add_log(state, f"📊 Daily limit {MAX_SIGNALS_DAY} reached")
        save_state(state); return

    # Refresh wallet balance
    avail_bal, wallet_bal = get_wallet_balance()
    if avail_bal is not None:
        state["balance"]        = round(avail_bal, 2)
        state["wallet_balance"] = round(wallet_bal, 2)
    else:
        add_error(state, "Could not fetch wallet balance")
        if not API_KEY or not API_SECRET:
            add_error(state, "API keys not configured — set BINANCE_FUTURES_API_KEY and BINANCE_FUTURES_SECRET")

    # Update pending orders count from Binance
    pending_count = get_all_open_orders_count()
    state["pending_orders_count"] = pending_count

    add_log(state, f"🔍 Scanning {len(WATCH_LIST)} coins | Balance: ${state['balance']:.2f}")
    state["bot_status"] = "scanning"
    state["last_scan"]  = datetime.utcnow().strftime("%H:%M:%S")
    save_state(state)

    found = 0; scan_results = []

    for coin in WATCH_LIST:
        if len(state["open_trades"]) >= MAX_OPEN_TRADES: break
        if state.get("signals_today", 0) >= MAX_SIGNALS_DAY: break

        htf_trend, htf_strength = get_htf_trend(coin)

        df_raw = get_klines(coin, "1h", DATA_LIMIT)
        if df_raw is None:
            scan_results.append(f"{coin}:klines_fail")
            add_log(state, f"⚠️ {coin} klines failed", "WARN")
            continue

        df = compute_indicators(df_raw)
        i  = len(df) - 1
        if i < 100:
            scan_results.append(f"{coin}:data_short")
            continue

        c = df.iloc[i]
        if any(pd.isna(c.get(k, float('nan'))) for k in ['ATR','EMA200','STOCHRSI','ADX']):
            scan_results.append(f"{coin}:indicator_nan")
            continue

        # Direction by HTF or ST
        if htf_trend == "BULL":
            directions = ["LONG"]
        elif htf_trend == "BEAR":
            directions = ["SHORT"]
        else:
            st_now = int(c.get('ST_DIR', 1) or 1)
            directions = ["LONG"] if st_now == 1 else ["SHORT"]

        for direction in directions:
            score, reasons = score_signal(df, i, direction, htf_trend, htf_strength)
            if score < MIN_SCORE:
                scan_results.append(f"{coin}:{direction}_sc{score}")
                continue

            entry = float(c['close'])
            sl, tp1, tp2, sl_pct, tp1_pct, rr = calculate_levels(df, i, direction, entry)
            if rr < MIN_RR:
                scan_results.append(f"{coin}:{direction}_rr{rr:.1f}_fail")
                continue

            # ── DUPLICATE SIGNAL CHECK ──
            with _lock:
                state = load_state()

            existing_same = [
                t for t in state["open_trades"]
                if t["symbol"] == coin and t["direction"] == direction
            ]

            if existing_same:
                # Same coin, same direction already open
                old_trade = existing_same[0]
                old_score = old_trade.get("score", 0)

                if score > old_score + 2:
                    # New score is significantly better (>2 points) — update SL/TP
                    info = get_exchange_info(coin)
                    if info:
                        new_sl_id, new_tp_id = update_sl_tp_orders(old_trade, sl, tp1, info)
                        with _lock:
                            state = load_state()
                            for t in state["open_trades"]:
                                if t["id"] == old_trade["id"]:
                                    t["sl"]          = round(sl, 6)
                                    t["tp1"]         = round(tp1, 6)
                                    t["tp2"]         = round(tp2, 6)
                                    t["sl_pct"]      = round(sl_pct, 3)
                                    t["tp1_pct"]     = round(tp1_pct, 3)
                                    t["rr"]          = round(rr, 2)
                                    t["score"]       = score
                                    t["reasons"]     = reasons[:6]
                                    t["sl_order_id"] = new_sl_id
                                    t["tp_order_id"] = new_tp_id
                                    break
                            add_log(state, f"🔄 {coin} {direction} levels updated — Score {old_score}→{score} | SL:{sl:.4f} TP:{tp1:.4f}")
                            save_state(state)
                        # No Telegram for signal updates — only actual trades
                        scan_results.append(f"{coin}:{direction}_UPDATED_sc{score}")
                    else:
                        scan_results.append(f"{coin}:{direction}_update_info_fail")
                else:
                    # Score not better enough — skip, no new order
                    scan_results.append(f"{coin}:{direction}_dup_skip_sc{score}vs{old_score}")
                continue  # Never place a second order for same coin+direction

            # ── NEW TRADE ──
            # Allocation
            alloc_pct = SCORE_ALLOC_PCT.get(min(score, 11), 2.0)
            alloc_usd = round(state["balance"] * (alloc_pct / 100.0), 2)
            alloc_usd = min(alloc_usd, state["balance"] * MAX_ALLOC_PCT)
            alloc_usd = round(alloc_usd, 2)

            if alloc_usd < MIN_TRADE_USDT:
                scan_results.append(f"{coin}:alloc_low${alloc_usd:.0f}")
                continue

            # Place order
            side = "BUY" if direction == "LONG" else "SELL"
            order_info, status = place_futures_order(coin, side, alloc_usd, entry, sl, tp1)

            if order_info is None:
                scan_results.append(f"{coin}:order_fail_{status[:20]}")
                continue

            trade = {
                "id":          f"{coin}_{direction}_{int(time.time())}",
                "symbol":      coin,
                "direction":   direction,
                "score":       score,
                "entry":       entry,
                "sl":          round(sl, 6),
                "tp1":         round(tp1, 6),
                "tp2":         round(tp2, 6),
                "sl_pct":      round(sl_pct, 3),
                "tp1_pct":     round(tp1_pct, 3),
                "rr":          round(rr, 2),
                "alloc_usd":   alloc_usd,
                "alloc_pct":   alloc_pct,
                "notional":    order_info["notional"],
                "qty":         order_info["qty"],
                "leverage":    LEVERAGE,
                "htf":         htf_trend,
                "reasons":     reasons[:6],
                "open_time":   datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
                "order_id":    order_info["order_id"],
                "sl_order_id": order_info["sl_order_id"],
                "tp_order_id": order_info["tp_order_id"],
                "tp1_hit":     False,
                "outcome":     "OPEN",
            }

            with _lock:
                state = load_state()
                state["open_trades"].append(trade)
                state["signals_today"] = state.get("signals_today", 0) + 1
                add_log(state, f"{'📈' if direction=='LONG' else '📉'} {direction} {coin} | Score:{score} Alloc:${alloc_usd:.2f} | SL:{sl:.4f} TP:{tp1:.4f} | ✅ #{order_info['order_id']}")
                save_state(state)

            scan_results.append(f"{coin}:{direction}_TRADE_sc{score}")
            # Telegram — only for actual new trades
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
            # Check bot_running flag (set by dashboard)
            with _lock:
                state = load_state()
            bot_running = state.get("bot_running", True)

            if bot_running:
                monitor_open_trades()
                if scan_counter % 15 == 0:
                    scan_once()
            else:
                # Stopped — just update status
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
