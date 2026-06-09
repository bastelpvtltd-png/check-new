import time, os, json, threading, logging
from datetime import datetime, timedelta
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import calendar

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger("BOT")

# ══════════════════════════════════════════════════════════════════
# CONFIG — .env or environment variables
# ══════════════════════════════════════════════════════════════════
API_KEY    = os.environ.get("BINANCE_TESTNET_API_KEY", "")
API_SECRET = os.environ.get("BINANCE_TESTNET_SECRET",  "")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID",   "")

# Public data endpoints — no auth needed, Render-accessible
PUBLIC_BASE  = "https://api.binance.us"          # real market data (public)
TESTNET_BASE = "https://testnet.binance.vision"   # Spot testnet for orders

WATCH_LIST = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT",
    "ADAUSDT","LINKUSDT","AVAXUSDT","DOTUSDT","MATICUSDT",
    "LTCUSDT","UNIUSDT","NEARUSDT","APTUSDT","INJUSDT",
    "OPUSDT","ARBUSDT","SUIUSDT","TIAUSDT","FETUSDT"
]
BLOCKED_COINS   = []
MIN_SCORE       = 8
MAX_SIGNALS_DAY = 15
COOLDOWN_BARS   = 4
MIN_RR          = 1.8
MAX_OPEN_TRADES = 8
DATA_LIMIT      = 300
SCORE_ALLOC = {6: 4.0, 7: 6.0, 8: 9.0, 9: 14.0, 10: 18.0}
MAX_ALLOC_PCT    = 0.20
MIN_TRADE_USDT   = 5.0
STARTING_BALANCE = 100_000.0

STATE_FILE = os.environ.get("STATE_FILE", "/tmp/bot_state.json")

# ══════════════════════════════════════════════════════════════════
# SESSION
# ══════════════════════════════════════════════════════════════════
def make_session():
    s = requests.Session()
    r = Retry(total=4, backoff_factor=1.5,
              status_forcelist=[429,500,502,503,504],
              allowed_methods=["GET","POST","DELETE"])
    a = HTTPAdapter(max_retries=r)
    s.mount("https://", a); s.mount("http://", a)
    return s

SESSION = make_session()

# ══════════════════════════════════════════════════════════════════
# STATE MANAGER
# ══════════════════════════════════════════════════════════════════
_lock = threading.Lock()

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {
            "balance": STARTING_BALANCE,
            "starting": STARTING_BALANCE,
            "open_trades": [],
            "closed_trades": [],
            "signals_today": 0,
            "signals_date": "",
            "bot_status": "starting",
            "last_scan": "",
            "wins": 0, "losses": 0, "bes": 0,
            "total_pnl": 0.0,
            "log": []
        }

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)

def add_log(state, msg, level="INFO"):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    entry = {"ts": ts, "msg": msg, "level": level}
    state["log"].insert(0, entry)
    state["log"] = state["log"][:200]
    log.info(msg)

# ══════════════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════════════
def tg(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chunk in [msg[i:i+4000] for i in range(0, len(msg), 4000)]:
        try:
            SESSION.post(url, json={"chat_id": TELEGRAM_CHAT, "text": chunk,
                                    "parse_mode": "Markdown"}, timeout=8)
        except: pass
        time.sleep(0.2)

# ══════════════════════════════════════════════════════════════════
# BINANCE API — PUBLIC DATA (api.binance.us, no auth)
# ══════════════════════════════════════════════════════════════════
def get_klines(symbol, interval="1h", limit=300):
    """Fetch OHLCV from public Binance.US endpoint — no auth needed."""
    try:
        r = SESSION.get(
            f"{PUBLIC_BASE}/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=12
        )
        if r.status_code != 200:
            log.warning(f"klines {symbol} HTTP {r.status_code}: {r.text[:120]}")
            return None
        rows = r.json()
        if not rows or isinstance(rows, dict):
            log.warning(f"klines {symbol} bad response: {str(rows)[:120]}")
            return None
        df = pd.DataFrame(rows, columns=[
            'Open_time','open','high','low','close','volume',
            'Close_time','qav','num_trades','taker_base','taker_quote','ignore'])
        for c in ['open','high','low','close','volume']:
            df[c] = pd.to_numeric(df[c], errors='coerce')
        df['Open_time'] = df['Open_time'].astype(int)
        df = df.dropna(subset=['open','high','low','close'])
        return df.reset_index(drop=True) if len(df) >= 50 else None
    except Exception as e:
        log.error(f"get_klines {symbol} error: {e}")
        return None

def get_price(symbol):
    """Get current price from public Binance.US endpoint."""
    try:
        r = SESSION.get(
            f"{PUBLIC_BASE}/api/v3/ticker/price",
            params={"symbol": symbol},
            timeout=6
        )
        if r.status_code == 200:
            return float(r.json()["price"])
    except Exception as e:
        log.error(f"get_price {symbol} error: {e}")
    return None

def get_htf_klines(symbol, interval, limit):
    """Fetch higher timeframe klines from public endpoint."""
    try:
        r = SESSION.get(
            f"{PUBLIC_BASE}/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=10
        )
        if r.status_code != 200 or not r.json() or isinstance(r.json(), dict):
            return None
        rows = r.json()
        df = pd.DataFrame(rows, columns=[
            'Open_time','open','high','low','close','volume',
            'Close_time','qav','num_trades','taker_base','taker_quote','ignore'])
        df['close'] = pd.to_numeric(df['close'])
        return df
    except Exception as e:
        log.error(f"htf_klines {symbol} {interval} error: {e}")
        return None

# ══════════════════════════════════════════════════════════════════
# BINANCE TESTNET — SIGNED ORDERS (testnet.binance.vision)
# ══════════════════════════════════════════════════════════════════
import hmac, hashlib
from urllib.parse import urlencode

def sign(params: dict) -> str:
    query = urlencode(params)
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def testnet_get(path, params=None, signed=False):
    if params is None: params = {}
    if signed:
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = sign(params)
    headers = {"X-MBX-APIKEY": API_KEY}
    try:
        r = SESSION.get(f"{TESTNET_BASE}{path}", params=params,
                        headers=headers, timeout=12)
        return r.json()
    except Exception as e:
        log.error(f"testnet GET {path} error: {e}")
        return None

def testnet_post(path, params):
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params)
    headers = {"X-MBX-APIKEY": API_KEY}
    try:
        r = SESSION.post(f"{TESTNET_BASE}{path}", params=params,
                         headers=headers, timeout=12)
        return r.json()
    except Exception as e:
        log.error(f"testnet POST {path} error: {e}")
        return None

def get_testnet_balance():
    """Get USDT balance from Spot testnet account."""
    if not API_KEY or not API_SECRET:
        return None
    resp = testnet_get("/api/v3/account", {}, signed=True)
    if isinstance(resp, dict) and "balances" in resp:
        for b in resp["balances"]:
            if b.get("asset") == "USDT":
                return float(b.get("free", 0))
    return None

def place_paper_order(symbol, side, alloc_usd, entry_price):
    """
    Pure paper trade — no actual order placed.
    Returns a mock order_id so state tracks it cleanly.
    If testnet keys are configured, also attempt a testnet spot order.
    """
    paper_id = f"paper_{symbol}_{int(time.time())}"

    if not API_KEY or not API_SECRET:
        return paper_id, False

    # Spot testnet: calculate quantity in base asset
    # Binance.US symbols always end in USDT
    qty = round(alloc_usd / entry_price, 6)
    params = {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": qty,
    }
    resp = testnet_post("/api/v3/order", params)
    if isinstance(resp, dict) and "orderId" in resp:
        return str(resp["orderId"]), True
    else:
        log.warning(f"Testnet order failed for {symbol}: {resp}")
        return paper_id, False

def cancel_open_orders_testnet(symbol):
    if not API_KEY or not API_SECRET:
        return
    try:
        testnet_post("/api/v3/openOrders", {"symbol": symbol})
    except: pass

# ══════════════════════════════════════════════════════════════════
# INDICATORS
# ══════════════════════════════════════════════════════════════════
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
    d['BB_PCT']   = (d['close'] - d['BB_LO']) / (d['BB_UP'] - d['BB_LO'] + 1e-9)
    d['BB_WIDTH'] = (d['BB_UP'] - d['BB_LO']) / (d['BB_MA'] + 1e-9)

    d['VOL_MA20'] = d['volume'].rolling(20).mean()

    hl  = d['high'] - d['low']
    hpc = (d['high'] - d['close'].shift(1)).abs()
    lpc = (d['low']  - d['close'].shift(1)).abs()
    d['ATR'] = pd.concat([hl, hpc, lpc], axis=1).max(axis=1).rolling(14).mean()

    rsi_min = d['RSI'].rolling(14).min()
    rsi_max = d['RSI'].rolling(14).max()
    d['STOCHRSI'] = (d['RSI'] - rsi_min) / ((rsi_max - rsi_min) + 1e-9)

    pdm = d['high'].diff().clip(lower=0)
    mdm = (-d['low'].diff()).clip(lower=0)
    pdm2 = pdm.where(pdm > mdm, 0)
    mdm2 = mdm.where(mdm > pdm, 0)
    d['PLUS_DI']  = 100 * (pdm2.rolling(14).mean() / (d['ATR'] + 1e-9))
    d['MINUS_DI'] = 100 * (mdm2.rolling(14).mean() / (d['ATR'] + 1e-9))
    dx = 100 * (d['PLUS_DI'] - d['MINUS_DI']).abs() / (d['PLUS_DI'] + d['MINUS_DI'] + 1e-9)
    d['ADX'] = dx.rolling(14).mean()

    hl2 = (d['high'] + d['low']) / 2
    ub = hl2 + 3.0 * d['ATR']
    lb = hl2 - 3.0 * d['ATR']
    st_dir = [1]*len(d); st_val = [0.0]*len(d)
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

    typ = (d['high'] + d['low'] + d['close']) / 3
    d['VWAP']   = (typ * d['volume']).rolling(24).sum() / d['volume'].rolling(24).sum()
    d['ROC5']   = d['close'].pct_change(5) * 100
    d['TENKAN'] = (d['high'].rolling(9).max()  + d['low'].rolling(9).min())  / 2
    d['KIJUN']  = (d['high'].rolling(26).max() + d['low'].rolling(26).min()) / 2
    return d

def get_htf_trend(symbol):
    scores = {"BULL": 0, "BEAR": 0}
    for tf, lim, w in [("4h", 80, 2), ("1d", 40, 1)]:
        df = get_htf_klines(symbol, tf, lim)
        if df is None or len(df) < 40:
            continue
        c = df['close']
        e20 = c.ewm(span=20, adjust=False).mean()
        e50 = c.ewm(span=50, adjust=False).mean()
        lc = c.iloc[-1]
        if lc > e20.iloc[-1] > e50.iloc[-1]: scores["BULL"] += w
        elif lc < e20.iloc[-1] < e50.iloc[-1]: scores["BEAR"] += w
        time.sleep(0.06)
    if scores["BULL"] >= 2: return "BULL", min(scores["BULL"], 3)
    if scores["BEAR"] >= 2: return "BEAR", min(scores["BEAR"], 3)
    return "NEUTRAL", 0

def score_signal(df, i, direction, htf_trend, htf_strength):
    c = df.iloc[i]; prev = df.iloc[i-1]
    score = 0; reasons = []

    if direction == "LONG" and htf_trend == "BULL":
        score += htf_strength; reasons.append(f"HTF BULL +{htf_strength}")
    elif direction == "SHORT" and htf_trend == "BEAR":
        score += htf_strength; reasons.append(f"HTF BEAR +{htf_strength}")
    elif htf_trend == "NEUTRAL":
        score += 1; reasons.append("HTF neutral")
    else:
        return 0, ["HTF counter-trend"]

    adx = float(c.get('ADX', 0) or 0)
    if adx < 18: return 0, [f"ADX {adx:.1f} flat"]
    if adx >= 30:   score += 3; reasons.append(f"ADX {adx:.1f} strong")
    elif adx >= 25: score += 2; reasons.append(f"ADX {adx:.1f} ok")
    else:           score += 1; reasons.append(f"ADX {adx:.1f}")

    st = int(c.get('ST_DIR', 0) or 0)
    if direction == "LONG":
        if st == 1:  score += 2; reasons.append("ST bull ✓")
        else:        score -= 1; reasons.append("ST bear ⚠")
    else:
        if st == -1: score += 2; reasons.append("ST bear ✓")
        else:        score -= 1; reasons.append("ST bull ⚠")

    cl = float(c['close']); prev_cl = float(prev['close'])
    e8  = float(c['EMA8']); e21 = float(c['EMA21'])
    e55 = float(c['EMA55']); e200 = float(c['EMA200'])

    if direction == "LONG":
        if cl > e8 > e21 > e55 > e200:   score += 4; reasons.append("EMA perfect bull")
        elif cl > e21 > e55 > e200:        score += 3; reasons.append("EMA full bull")
        elif cl > e21 > e55:               score += 2; reasons.append("EMA 21>55")
        elif cl > e21:                     score += 1; reasons.append("Above EMA21")
        if prev_cl < e8 and cl > e8:       score += 1; reasons.append("EMA8 reclaim ↑")
    else:
        if cl < e8 < e21 < e55 < e200:   score += 4; reasons.append("EMA perfect bear")
        elif cl < e21 < e55 < e200:        score += 3; reasons.append("EMA full bear")
        elif cl < e21 < e55:               score += 2; reasons.append("EMA 21<55")
        elif cl < e21:                     score += 1; reasons.append("Below EMA21")
        if prev_cl > e8 and cl < e8:       score += 1; reasons.append("EMA8 reclaim ↓")

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

    macd = float(c.get('MACD',0) or 0); macds = float(c.get('MACDS',0) or 0)
    pmacd= float(prev.get('MACD',0) or 0); pmacds = float(prev.get('MACDS',0) or 0)
    hist = float(c.get('MACD_HIST',0) or 0); p_hist = float(prev.get('MACD_HIST',0) or 0)
    if direction == "LONG":
        if macd > macds and pmacd <= pmacds: score += 2; reasons.append("MACD cross ↑")
        elif macd > macds: score += 1; reasons.append("MACD bull")
        if hist > 0 and hist > p_hist: score += 1; reasons.append("MACD hist ↑")
    else:
        if macd < macds and pmacd >= pmacds: score += 2; reasons.append("MACD cross ↓")
        elif macd < macds: score += 1; reasons.append("MACD bear")
        if hist < 0 and hist < p_hist: score += 1; reasons.append("MACD hist ↓")

    bbma = float(c.get('BB_MA', cl) or cl)
    bbw  = float(c.get('BB_WIDTH', 0) or 0)
    if bbma > 0 and bbw < 0.012: return 0, ["BB squeeze"]

    vm = float(c.get('VOL_MA20', 0) or 0)
    vr = c['volume'] / vm if vm > 0 else 1.0
    if vr > 2.0:   score += 3; reasons.append(f"Vol {vr:.1f}x")
    elif vr > 1.5: score += 2; reasons.append(f"Vol {vr:.1f}x")
    elif vr > 1.2: score += 1; reasons.append(f"Vol {vr:.1f}x")

    stoch = float(c.get('STOCHRSI', 0.5) or 0.5)
    if direction == "LONG"  and stoch > 0.92: return 0, ["StochRSI OB"]
    if direction == "SHORT" and stoch < 0.08: return 0, ["StochRSI OS"]

    return max(score, 0), reasons

def calculate_levels(df, i, direction, entry):
    c = df.iloc[i]
    atr = float(c['ATR']) if not pd.isna(c['ATR']) else entry * 0.015
    lb = max(0, i-20); rl = df['low'].iloc[lb:i]; rh = df['high'].iloc[lb:i]
    if direction == "LONG":
        atr_sl = entry - atr * 1.8
        sw_sl  = float(rl.min()) * 0.998 if len(rl) > 0 else atr_sl
        sl  = min(atr_sl, sw_sl); sl = min(sl, entry*0.985); sl = max(sl, entry*0.970)
        risk = entry - sl; tp1 = entry + risk * 2.0; tp2 = entry + risk * 3.5
    else:
        atr_sl = entry + atr * 1.8
        sw_sl  = float(rh.max()) * 1.002 if len(rh) > 0 else atr_sl
        sl  = max(atr_sl, sw_sl); sl = max(sl, entry*1.015); sl = min(sl, entry*1.030)
        risk = sl - entry; tp1 = entry - risk * 2.0; tp2 = entry - risk * 3.5
    sl_pct  = abs(entry-sl)/entry*100
    tp1_pct = abs(tp1-entry)/entry*100
    rr = tp1_pct / sl_pct if sl_pct > 0 else 0
    return sl, tp1, tp2, sl_pct, tp1_pct, rr

# ══════════════════════════════════════════════════════════════════
# TRADE MONITOR
# ══════════════════════════════════════════════════════════════════
def monitor_open_trades():
    with _lock:
        state = load_state()

    if not state["open_trades"]:
        return

    closed_ids = []
    for trade in state["open_trades"]:
        sym   = trade["symbol"]
        price = get_price(sym)
        if price is None:
            continue

        direction = trade["direction"]
        sl  = trade["sl"]
        tp1 = trade["tp1"]
        tp2 = trade["tp2"]
        entry = trade["entry"]

        outcome = None
        pnl_pct = 0.0

        if direction == "LONG":
            if not trade.get("tp1_hit") and price >= tp1:
                trade["tp1_hit"] = True
            if trade.get("tp1_hit") and price >= tp2:
                outcome = "TP2_HIT"
                pnl_pct = ((tp1-entry)/entry*100)*0.5 + ((tp2-entry)/entry*100)*0.5
            elif price <= sl:
                if trade.get("tp1_hit"):
                    outcome = "BREAKEVEN"
                    pnl_pct = (tp1-entry)/entry*100 * 0.5
                else:
                    outcome = "SL_HIT"
                    pnl_pct = (sl-entry)/entry*100
        else:
            if not trade.get("tp1_hit") and price <= tp1:
                trade["tp1_hit"] = True
            if trade.get("tp1_hit") and price <= tp2:
                outcome = "TP2_HIT"
                pnl_pct = ((entry-tp1)/entry*100)*0.5 + ((entry-tp2)/entry*100)*0.5
            elif price >= sl:
                if trade.get("tp1_hit"):
                    outcome = "BREAKEVEN"
                    pnl_pct = (entry-tp1)/entry*100 * 0.5
                else:
                    outcome = "SL_HIT"
                    pnl_pct = (entry-sl)/entry*100

        if outcome:
            alloc = trade["alloc_usd"]
            if outcome == "TP2_HIT":
                pnl_usd = alloc * abs(pnl_pct) / 100
            elif outcome == "SL_HIT":
                pnl_usd = -alloc * abs(pnl_pct) / 100
            else:
                pnl_usd = alloc * abs(pnl_pct) / 100

            with _lock:
                state = load_state()
                state["balance"] = round(state["balance"] + pnl_usd, 2)
                state["total_pnl"] = round(state.get("total_pnl", 0) + pnl_usd, 2)
                if outcome == "TP2_HIT": state["wins"] += 1
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

                em = "✅" if outcome=="TP2_HIT" else "🟡" if outcome=="BREAKEVEN" else "❌"
                add_log(state, f"{em} {sym} {direction} → {outcome} | P&L: ${pnl_usd:+.2f} | Bal: ${state['balance']:.2f}")
                save_state(state)

            tg(f"{em} *{sym} {direction} — {outcome}*\n"
               f"Entry: `${entry:.4f}` → Exit: `${price:.4f}`\n"
               f"P&L: *${pnl_usd:+.2f}* ({pnl_pct:+.2f}%)\n"
               f"New Balance: `${state['balance']:.2f}`")

    if closed_ids:
        with _lock:
            state = load_state()
            state["open_trades"] = [t for t in state["open_trades"] if t["id"] not in closed_ids]
            save_state(state)

# ══════════════════════════════════════════════════════════════════
# CONFIDENCE ESCALATION
# ══════════════════════════════════════════════════════════════════
def check_escalation(state, sym, direction, score, entry, sl, tp1, tp2, reasons):
    for trade in state["open_trades"]:
        if trade["symbol"] == sym and trade["direction"] == direction:
            if score > trade["score"] + 1:
                is_better = (direction == "LONG" and entry > trade["entry"]) or \
                            (direction == "SHORT" and entry < trade["entry"])
                if not is_better: return False

                avail = state["balance"] - sum(t["alloc_usd"] for t in state["open_trades"])
                extra = min(avail * 0.05, state["balance"] * 0.05)
                if extra < MIN_TRADE_USDT: return False

                trade["alloc_usd"] = round(trade["alloc_usd"] + extra, 2)
                trade["score"] = score
                trade["notes"] = trade.get("notes", "") + f" +PYRAMID@{entry:.4f}"
                add_log(state, f"🔼 PYRAMID {sym} {direction} +${extra:.2f} (score↑{score})")
                save_state(state)
                return True
    return False

# ══════════════════════════════════════════════════════════════════
# MAIN SCAN LOOP
# ══════════════════════════════════════════════════════════════════
def scan_once():
    with _lock:
        state = load_state()

    today = datetime.utcnow().strftime("%Y-%m-%d")
    if state.get("signals_date") != today:
        state["signals_today"] = 0
        state["signals_date"] = today

    open_count = len(state["open_trades"])
    if open_count >= MAX_OPEN_TRADES:
        add_log(state, f"⛔ Max {MAX_OPEN_TRADES} trades open — skip scan")
        save_state(state)
        return

    if state.get("signals_today", 0) >= MAX_SIGNALS_DAY:
        add_log(state, f"📊 Daily signal limit {MAX_SIGNALS_DAY} reached")
        save_state(state)
        return

    add_log(state, f"🔍 Scanning {len(WATCH_LIST)} coins...")
    state["bot_status"] = "scanning"
    state["last_scan"]  = datetime.utcnow().strftime("%H:%M:%S")
    save_state(state)

    active_coins = [c for c in WATCH_LIST if c not in BLOCKED_COINS]
    found = 0

    for coin in active_coins:
        if len(state["open_trades"]) >= MAX_OPEN_TRADES: break
        if state.get("signals_today", 0) >= MAX_SIGNALS_DAY: break

        existing = [t for t in state["open_trades"] if t["symbol"] == coin]
        if len(existing) >= 2: continue

        htf_trend, htf_strength = get_htf_trend(coin)
        if htf_trend == "NEUTRAL": continue

        df_raw = get_klines(coin, "1h", DATA_LIMIT)
        if df_raw is None:
            add_log(state, f"⚠️ {coin} — klines failed, skipping", "WARN")
            continue

        df = compute_indicators(df_raw)
        i  = len(df) - 1
        if i < 100: continue

        c = df.iloc[i]
        if any(pd.isna(c.get(k, float('nan'))) for k in ['ATR','EMA200','STOCHRSI','ADX']):
            continue

        if htf_trend == "BULL":
            directions = ["LONG"]
        elif htf_trend == "BEAR":
            directions = ["SHORT"]
        else:  # NEUTRAL — follow ST direction to pick one side only
            st_dir_now = int(df.iloc[-1].get('ST_DIR', 1) if hasattr(df.iloc[-1], 'get') else 1)
            directions = ["LONG"] if st_dir_now == 1 else ["SHORT"]
        for direction in directions:
            score, reasons = score_signal(df, i, direction, htf_trend, htf_strength)
            if score < MIN_SCORE: continue

            entry = float(c['close'])
            sl, tp1, tp2, sl_pct, tp1_pct, rr = calculate_levels(df, i, direction, entry)
            if rr < MIN_RR: continue

            with _lock:
                state = load_state()
                escalated = check_escalation(state, coin, direction, score, entry, sl, tp1, tp2, reasons)
            if escalated: continue

            alloc_pct = SCORE_ALLOC.get(min(max(score, 6), 10), 4.0)
            with _lock:
                state = load_state()
                avail  = state["balance"] - sum(t["alloc_usd"] for t in state["open_trades"])
            max_usd    = state["balance"] * MAX_ALLOC_PCT
            alloc_usd  = min(avail * (alloc_pct / 100.0), max_usd)
            alloc_usd  = round(alloc_usd, 2)

            if alloc_usd < MIN_TRADE_USDT:
                add_log(state, f"⚠️ {coin} skip — alloc ${alloc_usd:.2f} < min")
                continue

            side = "BUY" if direction == "LONG" else "SELL"
            order_id, order_ok = place_paper_order(coin, side, alloc_usd, entry)

            trade = {
                "id":        f"{coin}_{direction}_{int(time.time())}",
                "symbol":    coin,
                "direction": direction,
                "score":     score,
                "entry":     entry,
                "sl":        round(sl, 6),
                "tp1":       round(tp1, 6),
                "tp2":       round(tp2, 6),
                "sl_pct":    round(sl_pct, 3),
                "tp1_pct":   round(tp1_pct, 3),
                "rr":        round(rr, 2),
                "alloc_usd": alloc_usd,
                "alloc_pct": alloc_pct,
                "htf":       htf_trend,
                "reasons":   reasons[:6],
                "open_time": datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
                "order_id":  order_id,
                "order_ok":  order_ok,
                "tp1_hit":   False,
                "outcome":   "OPEN",
            }

            with _lock:
                state = load_state()
                state["open_trades"].append(trade)
                state["signals_today"] = state.get("signals_today", 0) + 1
                add_log(state, f"{'📈' if direction=='LONG' else '📉'} NEW {direction} {coin} | Score:{score} Alloc:${alloc_usd:.2f} | {'✅ Testnet order' if order_ok else '📝 Paper only'}")
                save_state(state)

            tg(f"{'📈' if direction=='LONG' else '📉'} *{direction} {coin}*\n"
               f"Score `{score}` | HTF `{htf_trend}` | RR `1:{rr:.1f}`\n"
               f"Entry `${entry:.4f}` | SL `${sl:.4f}`\n"
               f"TP1 `${tp1:.4f}` | TP2 `${tp2:.4f}`\n"
               f"Alloc: `${alloc_usd:.2f}` ({alloc_pct:.0f}%)\n"
               f"_{' | '.join(reasons[:4])}_\n"
               f"{'✅ Testnet order placed' if order_ok else '📝 Signal tracked (paper)'}")

            found += 1
            time.sleep(0.5)

    with _lock:
        state = load_state()
        state["bot_status"] = "idle"
        add_log(state, f"✅ Scan done — {found} new signals | Open: {len(state['open_trades'])}")
        save_state(state)

# ══════════════════════════════════════════════════════════════════
# MAIN LOOP
# ══════════════════════════════════════════════════════════════════
def run():
    log.info("🤖 Bot started")
    with _lock:
        state = load_state()
        state["bot_status"] = "running"
        add_log(state, "🚀 Bot started — Paper Trading (Public Data + Testnet)")
        save_state(state)

    tg("🚀 *Paper Trading Bot Started*\n"
       f"Balance: `${STARTING_BALANCE:,.0f}` USDT\n"
       f"Coins: `{len(WATCH_LIST)}` | Max trades: `{MAX_OPEN_TRADES}`\n"
       f"Data: `api.binance.us` | Orders: `testnet.binance.vision`")

    scan_counter = 0
    while True:
        try:
            monitor_open_trades()

            if scan_counter % 15 == 0:
                scan_once()

            scan_counter += 1
            time.sleep(60)

        except KeyboardInterrupt:
            log.info("Bot stopped")
            break
        except Exception as e:
            log.error(f"Main loop error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    run()