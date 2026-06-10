import os, json, hmac, hashlib, time
from flask import Flask, jsonify, request, render_template_string
from datetime import datetime
from urllib.parse import urlencode
import requests

STATE_FILE   = os.environ.get("STATE_FILE", "/tmp/bot_state.json")
API_KEY      = os.environ.get("BINANCE_FUTURES_API_KEY", "")
API_SECRET   = os.environ.get("BINANCE_FUTURES_SECRET",  "")
FUTURES_BASE = "https://testnet.binancefuture.com"

app = Flask(__name__)

# ── State helpers ──
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {
            "balance": 0.0, "wallet_balance": 0.0,
            "open_trades": [], "closed_trades": [],
            "bot_status": "offline", "last_scan": "",
            "wins": 0, "losses": 0, "bes": 0,
            "total_pnl": 0.0, "signals_today": 0,
            "log": [], "errors": [],
            "bot_running": True,
            "pending_orders_count": 0,
        }

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)

# ── Binance API helpers ──
def _sign(params):
    query = urlencode(params)
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def _bapi_get(path, params=None, signed=False):
    if params is None:
        params = {}
    if signed:
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = _sign(params)
    headers = {"X-MBX-APIKEY": API_KEY}
    try:
        r = requests.get(f"{FUTURES_BASE}{path}", params=params, headers=headers, timeout=10)
        return r.json()
    except:
        return None

def _bapi_post(path, params):
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = _sign(params)
    headers = {"X-MBX-APIKEY": API_KEY}
    try:
        r = requests.post(f"{FUTURES_BASE}{path}", params=params, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def _bapi_delete(path, params):
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = _sign(params)
    headers = {"X-MBX-APIKEY": API_KEY}
    try:
        r = requests.delete(f"{FUTURES_BASE}{path}", params=params, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_live_balance():
    """Fetch real-time balance from Binance Testnet."""
    if not API_KEY or not API_SECRET:
        return None, None
    data = _bapi_get("/fapi/v2/account", {}, signed=True)
    if data and "assets" in data:
        for a in data["assets"]:
            if a.get("asset") == "USDT":
                return float(a.get("availableBalance", 0)), float(a.get("walletBalance", 0))
    data2 = _bapi_get("/fapi/v2/balance", {}, signed=True)
    if isinstance(data2, list):
        for b in data2:
            if b.get("asset") == "USDT":
                return float(b.get("availableBalance", 0)), float(b.get("balance", 0))
    return None, None

def get_pending_orders_count():
    """Fetch count of all open/pending orders from Binance."""
    if not API_KEY or not API_SECRET:
        return 0
    data = _bapi_get("/fapi/v1/openOrders", {}, signed=True)
    if isinstance(data, list):
        return len(data)
    return 0

# ─────────────────────────────────────────────
# HTML
# ─────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Futures Bot</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@700;800&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:#080c14;--surface:#0e1520;--border:#1a2540;
    --accent:#00e5ff;--green:#00e676;--red:#ff1744;
    --yellow:#ffd600;--text:#c8d8f0;--muted:#4a6080;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:'Space Mono',monospace;min-height:100vh;font-size:13px}
  header{display:flex;align-items:center;justify-content:space-between;padding:16px 24px;border-bottom:1px solid var(--border);background:var(--surface);position:sticky;top:0;z-index:100}
  .logo{font-family:'Syne',sans-serif;font-size:20px;font-weight:800;color:var(--accent)}
  .logo span{color:var(--green)}
  .hright{display:flex;gap:10px;align-items:center}
  .pill{display:flex;align-items:center;gap:8px;padding:5px 12px;border-radius:20px;border:1px solid var(--border);font-size:11px;background:var(--bg)}
  .dot{width:8px;height:8px;border-radius:50%;background:var(--muted)}
  .dot.idle{background:var(--green);box-shadow:0 0 6px var(--green)}
  .dot.scanning{background:var(--accent);box-shadow:0 0 6px var(--accent);animation:pulse .8s infinite}
  .dot.running{background:var(--green);box-shadow:0 0 6px var(--green);animation:pulse 2s infinite}
  .dot.stopped{background:var(--red);box-shadow:0 0 6px var(--red)}
  .dot.error{background:var(--red);box-shadow:0 0 6px var(--red)}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}

  /* Start / Stop buttons */
  .btn{cursor:pointer;border:none;border-radius:8px;padding:7px 18px;font-family:'Space Mono',monospace;font-size:11px;font-weight:700;letter-spacing:.5px;transition:all .2s}
  .btn-start{background:var(--green);color:#000}
  .btn-start:hover{opacity:.85}
  .btn-stop{background:var(--red);color:#fff}
  .btn-stop:hover{opacity:.85}
  .btn-sm{padding:4px 10px;font-size:10px;border-radius:5px;cursor:pointer;border:none;font-family:'Space Mono',monospace;font-weight:700}
  .btn-close{background:#ff174422;color:var(--red);border:1px solid var(--red);}
  .btn-close:hover{background:var(--red);color:#fff}
  .btn:disabled,.btn-sm:disabled{opacity:.4;cursor:not-allowed}

  .main{padding:18px 22px}
  .stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:10px;margin-bottom:16px}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px;position:relative;overflow:hidden}
  .card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px}
  .card.a::before{background:var(--accent)}.card.g::before{background:var(--green)}
  .card.r::before{background:var(--red)}.card.y::before{background:var(--yellow)}
  .clabel{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px}
  .cval{font-family:'Syne',sans-serif;font-size:22px;font-weight:800}
  .csub{color:var(--muted);font-size:10px;margin-top:3px}
  .panels{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}
  @media(max-width:860px){.panels{grid-template-columns:1fr}}
  .panel{background:var(--surface);border:1px solid var(--border);border-radius:8px;overflow:hidden}
  .ph{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-bottom:1px solid var(--border);font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--accent);font-weight:700}
  .badge{background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:2px 8px;font-size:10px;color:var(--text)}
  .tlist{max-height:420px;overflow-y:auto}
  .trow{padding:9px 14px;border-bottom:1px solid var(--border);display:grid;grid-template-columns:1fr 80px 55px 90px 75px 60px;gap:6px;align-items:center}
  .trow:hover{background:rgba(0,229,255,.03)}
  .trow.open{border-left:2px solid var(--accent)}
  .trow.win{border-left:2px solid var(--green)}
  .trow.loss{border-left:2px solid var(--red)}
  .trow.be{border-left:2px solid var(--yellow)}
  .tcoin{font-weight:700;font-size:12px}
  .treason{font-size:9px;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .tdir.long{color:var(--green)}.tdir.short{color:var(--red)}
  .tpnl.pos{color:var(--green)}.tpnl.neg{color:var(--red)}.tpnl.neu{color:var(--yellow)}
  .lev{font-size:9px;color:var(--muted)}
  .llist{max-height:220px;overflow-y:auto;font-size:11px}
  .le{padding:5px 14px;border-bottom:1px solid var(--border);display:flex;gap:10px;align-items:baseline}
  .lts{color:var(--muted);font-size:10px;flex-shrink:0}
  .lm.ERROR{color:var(--red)}.lm.WARN{color:var(--yellow)}
  .elist{max-height:150px;overflow-y:auto;font-size:11px}
  .ee{padding:5px 14px;border-bottom:1px solid var(--border);color:var(--red);font-size:10px}
  .empty{padding:24px;text-align:center;color:var(--muted);font-size:11px}
  .pbar{background:var(--bg);border-radius:4px;height:3px;margin-top:6px}
  .pbfill{height:100%;border-radius:4px;background:var(--green);transition:width .5s}
  ::-webkit-scrollbar{width:4px}::-webkit-scrollbar-track{background:var(--bg)}::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}
  .notify{position:fixed;bottom:20px;right:20px;padding:10px 18px;border-radius:8px;font-size:12px;font-family:'Space Mono',monospace;z-index:999;opacity:0;transition:opacity .3s}
  .notify.show{opacity:1}
  .notify.ok{background:#00e676;color:#000}
  .notify.err{background:#ff1744;color:#fff}
</style>
</head>
<body>
<header>
  <div class="logo">FUTURES<span>BOT</span></div>
  <div class="hright">
    <span style="color:var(--muted);font-size:10px" id="upd">—</span>
    <div class="pill"><div class="dot" id="sdot"></div><span id="stxt">—</span></div>
    <button class="btn btn-start" id="btnStart" onclick="botControl('start')">▶ START</button>
    <button class="btn btn-stop"  id="btnStop"  onclick="botControl('stop')">⏹ STOP</button>
  </div>
</header>
<div class="main">
  <div class="stat-grid" id="sg"></div>
  <div class="panels">
    <div class="panel">
      <div class="ph">Open Trades <span class="badge" id="oc">0</span></div>
      <div class="tlist" id="ol"></div>
    </div>
    <div class="panel">
      <div class="ph">Closed Trades <span class="badge" id="cc">0</span></div>
      <div class="tlist" id="cl"></div>
    </div>
    <div class="panel" style="grid-column:1/-1">
      <div class="ph">Live Log <span class="badge" id="sc">—</span></div>
      <div class="llist" id="ll"></div>
    </div>
    <div class="panel" style="grid-column:1/-1" id="errPanel">
      <div class="ph" style="color:var(--red)">Errors <span class="badge" id="ec">0</span></div>
      <div class="elist" id="el"></div>
    </div>
  </div>
</div>
<div class="notify" id="notify"></div>

<script>
const f2=(n,d=2)=>typeof n==='number'?n.toFixed(d):'—';
const fu=n=>'$'+f2(Math.abs(n)).replace(/\\B(?=(\\d{3})+(?!\\d))/g,',');

function showNotify(msg, type='ok'){
  const el=document.getElementById('notify');
  el.textContent=msg; el.className='notify show '+type;
  setTimeout(()=>el.className='notify',2500);
}

async function botControl(action){
  document.getElementById('btnStart').disabled=true;
  document.getElementById('btnStop').disabled=true;
  try{
    const r=await fetch('/api/bot/'+action, {method:'POST'});
    const d=await r.json();
    showNotify(d.msg || action+' sent', d.ok?'ok':'err');
  }catch(e){showNotify('Error: '+e,'err')}
  setTimeout(()=>{
    document.getElementById('btnStart').disabled=false;
    document.getElementById('btnStop').disabled=false;
  },1500);
  setTimeout(refresh,1000);
}

async function closeTrade(tradeId){
  if(!confirm('Close this trade at market price?')) return;
  try{
    const r=await fetch('/api/trade/close', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({trade_id: tradeId})
    });
    const d=await r.json();
    showNotify(d.msg||'Done', d.ok?'ok':'err');
  }catch(e){showNotify('Error: '+e,'err')}
  setTimeout(refresh,1500);
}

function stats(s){
  const pnl=s.total_pnl||0;
  const tot=s.wins+s.losses+s.bes;
  const wr=tot>0?(s.wins/tot*100):0;
  const pc=pnl>=0?'var(--green)':'var(--red)';
  const avail=typeof s.live_balance==='number'?fu(s.live_balance):fu(s.balance);
  const wallet=typeof s.live_wallet==='number'?fu(s.live_wallet):fu(s.wallet_balance||0);
  const pending=s.pending_orders_count||0;
  document.getElementById('sg').innerHTML=`
    <div class="card a">
      <div class="clabel">Available (Live)</div>
      <div class="cval" style="color:var(--accent)">${avail}</div>
      <div class="csub">Wallet: ${wallet}</div>
    </div>
    <div class="card ${pnl>=0?'g':'r'}">
      <div class="clabel">Total P&amp;L</div>
      <div class="cval" style="color:${pc}">${pnl>=0?'+':''}${fu(pnl)}</div>
      <div class="csub">Leveraged PnL</div>
    </div>
    <div class="card g">
      <div class="clabel">Win Rate</div>
      <div class="cval" style="color:var(--green)">${f2(wr,1)}%</div>
      <div class="csub">✅${s.wins} 🟡${s.bes} ❌${s.losses}</div>
      <div class="pbar"><div class="pbfill" style="width:${wr}%"></div></div>
    </div>
    <div class="card y">
      <div class="clabel">Today Signals</div>
      <div class="cval" style="color:var(--yellow)">${s.signals_today||0}/15</div>
    </div>
    <div class="card a">
      <div class="clabel">Open / Max</div>
      <div class="cval">${s.open_trades.length}/15</div>
    </div>
    <div class="card ${pending>0?'y':'g'}">
      <div class="clabel">Pending Orders</div>
      <div class="cval" style="color:${pending>0?'var(--yellow)':'var(--green)'}">${pending}</div>
      <div class="csub">On Binance</div>
    </div>
    <div class="card g">
      <div class="clabel">Total Closed</div>
      <div class="cval">${s.closed_trades.length}</div>
    </div>`;
}

function trow(t,isOpen){
  const d=t.direction;
  const dc=d==='LONG'?'long':'short';
  const ar=d==='LONG'?'▲':'▼';
  const pnl=t.pnl_usd||0;
  const pc=pnl>0?'pos':pnl<0?'neg':'neu';
  const rc=isOpen?'open':t.outcome==='TP2_HIT'?'win':t.outcome==='SL_HIT'?'loss':'be';
  const rs=(t.reasons||[]).slice(0,3).join(' | ');
  const pstr=isOpen?'—':`<span class="tpnl ${pc}">${pnl>=0?'+':''}$${Math.abs(pnl).toFixed(2)}</span>`;
  const out=isOpen?`<span style="color:var(--accent)">OPEN</span>`:t.outcome;
  const notional=t.notional?`<br><span class="lev">×${t.leverage||1} = $${t.notional.toFixed(0)}</span>`:'';
  const closeBtn=isOpen
    ?`<button class="btn-sm btn-close" onclick="closeTrade('${t.id}')">✕ CLOSE</button>`
    :`<span style="font-size:9px;color:var(--muted)">${t.close_time||''}</span>`;
  return `<div class="trow ${rc}">
    <div><div class="tcoin">${t.symbol.replace('USDT','')}</div><div class="treason">${rs}</div></div>
    <div class="tdir ${dc}">${ar} ${d}</div>
    <div style="color:var(--accent)">${t.score}/10</div>
    <div>${pstr}${notional}</div>
    <div style="font-size:10px">${out}</div>
    <div>${closeBtn}</div>
  </div>`;
}

function trades(s){
  document.getElementById('oc').textContent=s.open_trades.length;
  document.getElementById('cc').textContent=s.closed_trades.length;
  document.getElementById('ol').innerHTML=s.open_trades.length
    ?s.open_trades.map(t=>trow(t,true)).join('')
    :'<div class="empty">No open trades</div>';
  document.getElementById('cl').innerHTML=s.closed_trades.length
    ?s.closed_trades.slice(0,60).map(t=>trow(t,false)).join('')
    :'<div class="empty">No closed trades yet</div>';
}

function logs(s){
  document.getElementById('ll').innerHTML=(s.log||[]).slice(0,60).map(l=>
    `<div class="le"><span class="lts">${l.ts}</span><span class="lm ${l.level}">${l.msg}</span></div>`
  ).join('')||'<div class="empty">No logs</div>';
}

function errs(s){
  const errs=s.errors||[];
  document.getElementById('ec').textContent=errs.length;
  document.getElementById('errPanel').style.display=errs.length?'':'none';
  document.getElementById('el').innerHTML=errs.slice(0,20).map(e=>
    `<div class="ee">${e.ts} — ${e.msg}</div>`
  ).join('');
}

function status(s){
  const st=s.bot_status||'offline';
  const running=s.bot_running!==false;
  document.getElementById('sdot').className='dot '+st;
  document.getElementById('stxt').textContent=st.toUpperCase();
  document.getElementById('sc').textContent='Scan: '+(s.last_scan||'—');
  document.getElementById('upd').textContent=new Date().toLocaleTimeString();
  document.getElementById('btnStart').disabled=running;
  document.getElementById('btnStop').disabled=!running;
}

async function refresh(){
  try{
    const r=await fetch('/api/state');
    const s=await r.json();
    stats(s); trades(s); logs(s); errs(s); status(s);
  }catch(e){console.error(e)}
}

refresh();
setInterval(refresh,5000);
</script>
</body>
</html>"""

# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────
@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/state")
def api_state():
    state = load_state()
    # Fetch live balance from Binance every request
    avail, wallet = get_live_balance()
    if avail is not None:
        state["live_balance"] = round(avail, 2)
        state["live_wallet"]  = round(wallet, 2)
        # Also update state balance so bot picks it up
        state["balance"]        = round(avail, 2)
        state["wallet_balance"] = round(wallet, 2)
        save_state(state)
    # Fetch pending orders count
    pending = get_pending_orders_count()
    state["pending_orders_count"] = pending
    return jsonify(state)

@app.route("/api/bot/start", methods=["POST"])
def bot_start():
    state = load_state()
    state["bot_running"] = True
    state["bot_status"]  = "running"
    ts = datetime.utcnow().strftime("%H:%M:%S")
    state["log"].insert(0, {"ts": ts, "msg": "▶ Bot STARTED via dashboard", "level": "INFO"})
    state["log"] = state["log"][:300]
    save_state(state)
    return jsonify({"ok": True, "msg": "Bot started"})

@app.route("/api/bot/stop", methods=["POST"])
def bot_stop():
    state = load_state()
    state["bot_running"] = False
    state["bot_status"]  = "stopped"
    ts = datetime.utcnow().strftime("%H:%M:%S")
    state["log"].insert(0, {"ts": ts, "msg": "⏹ Bot STOPPED via dashboard", "level": "INFO"})
    state["log"] = state["log"][:300]
    save_state(state)
    return jsonify({"ok": True, "msg": "Bot stopped"})

@app.route("/api/trade/close", methods=["POST"])
def api_close_trade():
    data     = request.get_json(silent=True) or {}
    trade_id = data.get("trade_id", "")

    state = load_state()
    trade = next((t for t in state["open_trades"] if t["id"] == trade_id), None)
    if not trade:
        return jsonify({"ok": False, "msg": "Trade not found"})

    symbol    = trade["symbol"]
    direction = trade["direction"]
    qty       = trade.get("qty", 0)
    close_side = "SELL" if direction == "LONG" else "BUY"

    # Cancel SL/TP orders first
    for oid in [trade.get("sl_order_id"), trade.get("tp_order_id")]:
        if oid:
            _bapi_delete("/fapi/v1/order", {"symbol": symbol, "orderId": oid})
            time.sleep(0.15)

    if qty <= 0:
        return jsonify({"ok": False, "msg": "qty is zero — cannot close"})

    # Market close order
    resp = _bapi_post("/fapi/v1/order", {
        "symbol":       symbol,
        "side":         close_side,
        "type":         "MARKET",
        "quantity":     qty,
        "positionSide": "BOTH",
        "reduceOnly":   "true",
    })

    if not isinstance(resp, dict) or "orderId" not in resp:
        return jsonify({"ok": False, "msg": f"Close failed: {resp}"})

    # Get current price for PnL
    price_data = _bapi_get("/fapi/v1/ticker/price", {"symbol": symbol})
    exit_price = float(price_data["price"]) if price_data and "price" in price_data else trade["entry"]

    entry    = trade["entry"]
    alloc    = trade["alloc_usd"]
    notional = trade.get("notional", alloc)
    if direction == "LONG":
        pnl_pct = (exit_price - entry) / entry * 100
    else:
        pnl_pct = (entry - exit_price) / entry * 100
    pnl_usd = round(notional * pnl_pct / 100, 2)

    # Update state
    state = load_state()
    state["total_pnl"] = round(state.get("total_pnl", 0) + pnl_usd, 2)
    if pnl_usd > 0:   state["wins"]   += 1
    elif pnl_usd < 0: state["losses"] += 1
    else:              state["bes"]    += 1

    trade["outcome"]    = "MANUAL_CLOSE"
    trade["pnl_usd"]    = pnl_usd
    trade["pnl_pct"]    = round(pnl_pct, 3)
    trade["close_time"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    trade["exit_price"] = exit_price
    state["closed_trades"].insert(0, trade)
    state["closed_trades"] = state["closed_trades"][:500]
    state["open_trades"]   = [t for t in state["open_trades"] if t["id"] != trade_id]

    ts = datetime.utcnow().strftime("%H:%M:%S")
    em = "✅" if pnl_usd >= 0 else "❌"
    msg = f"{em} MANUAL CLOSE {symbol} {direction} | Exit: ${exit_price:.4f} | P&L: ${pnl_usd:+.2f}"
    state["log"].insert(0, {"ts": ts, "msg": msg, "level": "INFO"})
    state["log"] = state["log"][:300]
    save_state(state)

    return jsonify({"ok": True, "msg": f"Closed {symbol} — P&L: ${pnl_usd:+.2f}"})

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "time": datetime.utcnow().isoformat()})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
