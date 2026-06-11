import os, json, hmac, hashlib, time
from flask import Flask, jsonify, request, render_template_string
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
import requests

STATE_FILE   = os.environ.get("STATE_FILE", "/tmp/bot_state.json")
API_KEY      = os.environ.get("BINANCE_FUTURES_API_KEY", "")
API_SECRET   = os.environ.get("BINANCE_FUTURES_SECRET",  "")
FUTURES_BASE = "https://testnet.binancefuture.com"
LEVERAGE     = 1

SL_TZ = timezone(timedelta(hours=5, minutes=30))
def dt_sl():
    return datetime.now(SL_TZ).strftime("%Y-%m-%d %H:%M")
def ts_sl():
    return datetime.now(SL_TZ).strftime("%H:%M:%S")

app = Flask(__name__)

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
            "total_pnl": 0.0,
            "log": [], "errors": [],
            "bot_running": True,
            "pending_orders_count": 0,
            "trade_history": [],
        }

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)

def _sign(params):
    query = urlencode(params)
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def _bget(path, params=None, signed=False):
    if params is None: params = {}
    if signed:
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = _sign(params)
    try:
        r = requests.get(f"{FUTURES_BASE}{path}", params=params,
                         headers={"X-MBX-APIKEY": API_KEY}, timeout=10)
        return r.json()
    except:
        return None

def _bpost(path, params):
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = _sign(params)
    try:
        r = requests.post(f"{FUTURES_BASE}{path}", params=params,
                          headers={"X-MBX-APIKEY": API_KEY}, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def _bdel_algo(algo_id):
    params = {"algoId": algo_id, "timestamp": int(time.time() * 1000)}
    params["signature"] = _sign(params)
    try:
        r = requests.delete(f"{FUTURES_BASE}/fapi/v1/algoOrder", params=params,
                            headers={"X-MBX-APIKEY": API_KEY}, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def _bdel(path, params):
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = _sign(params)
    try:
        r = requests.delete(f"{FUTURES_BASE}{path}", params=params,
                            headers={"X-MBX-APIKEY": API_KEY}, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_live_balance():
    if not API_KEY or not API_SECRET:
        return None, None
    data = _bget("/fapi/v2/account", {}, signed=True)
    if data and "assets" in data:
        for a in data["assets"]:
            if a.get("asset") == "USDT":
                return float(a.get("availableBalance", 0)), float(a.get("walletBalance", 0))
    data2 = _bget("/fapi/v2/balance", {}, signed=True)
    if isinstance(data2, list):
        for b in data2:
            if b.get("asset") == "USDT":
                return float(b.get("availableBalance", 0)), float(b.get("balance", 0))
    return None, None

def get_pending_orders():
    """Returns full list of open orders from Binance."""
    if not API_KEY or not API_SECRET:
        return []
    data = _bget("/fapi/v1/openOrders", {}, signed=True)
    return data if isinstance(data, list) else []

def get_binance_positions():
    if not API_KEY or not API_SECRET:
        return []
    data = _bget("/fapi/v2/positionRisk", {}, signed=True)
    if not isinstance(data, list):
        return []
    return [p for p in data if abs(float(p.get("positionAmt", 0))) > 0]

def get_live_prices(symbols):
    """Fetch current prices for a list of symbols."""
    prices = {}
    for sym in symbols:
        try:
            data = _bget("/fapi/v1/ticker/price", {"symbol": sym})
            if data and "price" in data:
                prices[sym] = float(data["price"])
        except:
            pass
    return prices

# ─────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Futures Bot Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@700;800&display=swap" rel="stylesheet">
<style>
  :root{--bg:#080c14;--surface:#0e1520;--border:#1a2540;--accent:#00e5ff;--green:#00e676;--red:#ff1744;--yellow:#ffd600;--orange:#ff9100;--text:#c8d8f0;--muted:#4a6080;--card2:#111927}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:'Space Mono',monospace;min-height:100vh;font-size:13px}
  header{display:flex;align-items:center;justify-content:space-between;padding:14px 22px;border-bottom:1px solid var(--border);background:var(--surface);position:sticky;top:0;z-index:100;flex-wrap:wrap;gap:8px}
  .logo{font-family:'Syne',sans-serif;font-size:20px;font-weight:800;color:var(--accent)}.logo span{color:var(--green)}
  .hright{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  .pill{display:flex;align-items:center;gap:7px;padding:5px 12px;border-radius:20px;border:1px solid var(--border);font-size:11px;background:var(--bg)}
  .dot{width:8px;height:8px;border-radius:50%;background:var(--muted)}
  .dot.idle{background:var(--green);box-shadow:0 0 6px var(--green)}
  .dot.scanning{background:var(--accent);box-shadow:0 0 6px var(--accent);animation:pulse .8s infinite}
  .dot.running{background:var(--green);box-shadow:0 0 6px var(--green);animation:pulse 2s infinite}
  .dot.stopped{background:var(--red);box-shadow:0 0 6px var(--red)}
  .dot.error,.dot.offline{background:var(--red);box-shadow:0 0 6px var(--red)}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
  .btn{cursor:pointer;border:none;border-radius:8px;padding:7px 15px;font-family:'Space Mono',monospace;font-size:11px;font-weight:700;letter-spacing:.5px;transition:all .2s}
  .btn-green{background:var(--green);color:#000}.btn-green:hover{opacity:.85}
  .btn-red{background:var(--red);color:#fff}.btn-red:hover{opacity:.85}
  .btn-accent{background:rgba(0,229,255,.15);color:var(--accent);border:1px solid var(--accent)}.btn-accent:hover{background:rgba(0,229,255,.25)}
  .btn-yellow{background:rgba(255,214,0,.15);color:var(--yellow);border:1px solid var(--yellow)}.btn-yellow:hover{background:rgba(255,214,0,.25)}
  .btn:disabled{opacity:.35;cursor:not-allowed}
  .btn-sm{padding:3px 9px;font-size:10px;border-radius:5px;cursor:pointer;border:none;font-family:'Space Mono',monospace;font-weight:700;transition:all .2s}
  .btn-close-sm{background:#ff174420;color:var(--red);border:1px solid #ff174460}.btn-close-sm:hover{background:var(--red);color:#fff}
  .trade-toggle{display:flex;align-items:center;gap:8px;padding:5px 12px;border-radius:20px;border:1px solid var(--border);font-size:11px;cursor:pointer;user-select:none}
  .toggle-track{width:34px;height:18px;border-radius:9px;background:var(--red);position:relative;transition:background .3s}
  .toggle-track.on{background:var(--green)}
  .toggle-thumb{width:14px;height:14px;border-radius:50%;background:#fff;position:absolute;top:2px;left:2px;transition:left .3s}
  .toggle-track.on .toggle-thumb{left:18px}
  .main{padding:16px 20px}
  .stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px;margin-bottom:14px}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:13px;position:relative;overflow:hidden}
  .card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px}
  .card.a::before{background:var(--accent)}.card.g::before{background:var(--green)}
  .card.r::before{background:var(--red)}.card.y::before{background:var(--yellow)}.card.o::before{background:var(--orange)}
  .clabel{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:1px;margin-bottom:5px}
  .cval{font-family:'Syne',sans-serif;font-size:21px;font-weight:800}
  .csub{color:var(--muted);font-size:10px;margin-top:3px}
  .pbar{background:var(--bg);border-radius:4px;height:3px;margin-top:6px}
  .pbfill{height:100%;border-radius:4px;background:var(--green);transition:width .5s}
  /* TABS */
  .tabs{display:flex;gap:2px;margin-bottom:14px;border-bottom:1px solid var(--border)}
  .tab{padding:9px 18px;font-size:11px;font-weight:700;letter-spacing:.5px;cursor:pointer;border-bottom:2px solid transparent;color:var(--muted);transition:all .2s;text-transform:uppercase}
  .tab.active{color:var(--accent);border-bottom-color:var(--accent)}
  .tab:hover:not(.active){color:var(--text)}
  .tab-panel{display:none}.tab-panel.active{display:block}
  /* PANELS */
  .panel{background:var(--surface);border:1px solid var(--border);border-radius:8px;overflow:hidden;margin-bottom:14px}
  .ph{display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid var(--border);font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--accent);font-weight:700}
  .ph-right{margin-left:auto;display:flex;gap:6px;align-items:center}
  .badge{background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:2px 8px;font-size:10px;color:var(--text)}
  /* TRADE TABLE */
  .tlist{max-height:460px;overflow-y:auto}
  .trow{padding:9px 14px;border-bottom:1px solid var(--border);display:grid;gap:6px;align-items:start}
  .trow-open{grid-template-columns:1fr 70px 48px 100px 80px 70px}
  .trow-closed{grid-template-columns:1fr 70px 48px 100px 80px 100px}
  .trow:hover{background:rgba(0,229,255,.03)}
  .trow.open{border-left:2px solid var(--accent)}
  .trow.win{border-left:2px solid var(--green)}
  .trow.loss{border-left:2px solid var(--red)}
  .trow.be{border-left:2px solid var(--yellow)}
  .trow.synced{border-left:2px solid var(--muted)}
  .tcoin{font-weight:700;font-size:12px}
  .treason{font-size:9px;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:180px}
  .tdir.long{color:var(--green)}.tdir.short{color:var(--red)}
  .tpnl.pos{color:var(--green)}.tpnl.neg{color:var(--red)}.tpnl.neu{color:var(--yellow)}
  .lev{font-size:9px;color:var(--muted)}
  /* OPEN ORDERS TABLE */
  .oo-table{width:100%;border-collapse:collapse;font-size:11px}
  .oo-table th{padding:8px 12px;text-align:left;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid var(--border);font-weight:400}
  .oo-table td{padding:7px 12px;border-bottom:1px solid var(--border)}
  .oo-table tr:hover td{background:rgba(0,229,255,.02)}
  .oo-type-sl{color:var(--red)}.oo-type-tp{color:var(--green)}.oo-type-other{color:var(--yellow)}
  /* LOG */
  .llist{max-height:240px;overflow-y:auto;font-size:11px}
  .le{padding:5px 14px;border-bottom:1px solid var(--border);display:flex;gap:10px;align-items:baseline}
  .lts{color:var(--muted);font-size:10px;flex-shrink:0}
  .lm.ERROR{color:var(--red)}.lm.WARN{color:var(--yellow)}
  /* ERRORS */
  .elist{max-height:150px;overflow-y:auto;font-size:11px}
  .ee{padding:5px 14px;border-bottom:1px solid var(--border);color:var(--red);font-size:10px}
  /* HISTORY */
  .hist-row{padding:8px 14px;border-bottom:1px solid var(--border);display:grid;grid-template-columns:120px 80px 50px 90px 1fr 80px;gap:6px;align-items:center;font-size:11px}
  .hist-row:hover{background:rgba(0,229,255,.03)}
  .hist-outcome.WIN{color:var(--green)}.hist-outcome.LOSS{color:var(--red)}.hist-outcome.BREAKEVEN{color:var(--yellow)}
  /* STATUS BAR */
  .status-bar{background:var(--card2);border:1px solid var(--border);border-radius:6px;padding:8px 14px;margin-bottom:12px;font-size:11px;display:flex;gap:16px;flex-wrap:wrap;align-items:center}
  .sb-item{display:flex;gap:5px;align-items:center}
  .sb-label{color:var(--muted);font-size:10px}
  /* PORTFOLIO VALUE */
  .portfolio-bar{background:var(--card2);border:1px solid var(--border);border-radius:8px;padding:12px 16px;margin-bottom:14px;display:flex;gap:24px;flex-wrap:wrap;align-items:center}
  .pv-item{display:flex;flex-direction:column;gap:3px}
  .pv-label{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.5px}
  .pv-val{font-family:'Syne',sans-serif;font-size:18px;font-weight:800}
  .pv-div{width:1px;background:var(--border);height:36px}
  /* MISC */
  .sync-tag{font-size:9px;color:var(--muted);background:var(--bg);border:1px solid var(--border);border-radius:3px;padding:1px 4px;margin-left:4px}
  .tp1-tag{font-size:9px;color:var(--green);margin-left:4px}
  .empty{padding:24px;text-align:center;color:var(--muted);font-size:11px}
  .notify{position:fixed;bottom:20px;right:20px;padding:10px 18px;border-radius:8px;font-size:12px;font-family:'Space Mono',monospace;z-index:999;opacity:0;transition:opacity .3s;pointer-events:none}
  .notify.show{opacity:1}
  .notify.ok{background:var(--green);color:#000}.notify.err{background:var(--red);color:#fff}.notify.info{background:var(--accent);color:#000}
  ::-webkit-scrollbar{width:4px}::-webkit-scrollbar-track{background:var(--bg)}::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}
  .live-pnl-pos{color:var(--green)}.live-pnl-neg{color:var(--red)}.live-pnl-neu{color:var(--yellow)}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  @media(max-width:860px){.grid2{grid-template-columns:1fr}.trow-open,.trow-closed{grid-template-columns:1fr 70px;}.hist-row{grid-template-columns:1fr 1fr}}
</style>
</head>
<body>
<header>
  <div class="logo">FUTURES<span>BOT</span></div>
  <div class="hright">
    <span style="color:var(--muted);font-size:10px" id="upd">—</span>
    <div class="pill"><div class="dot" id="sdot"></div><span id="stxt">—</span></div>
    <!-- Trade Start/Stop Toggle -->
    <div class="trade-toggle" onclick="toggleTrading()" id="tradeToggle" title="Enable/Disable trading">
      <div class="toggle-track" id="toggleTrack"><div class="toggle-thumb"></div></div>
      <span id="toggleLabel" style="font-size:11px;font-weight:700">TRADING ON</span>
    </div>
    <button class="btn btn-accent" id="btnScan" onclick="manualScan()">⚡ SCAN</button>
    <button class="btn btn-green" id="btnStart" onclick="botControl('start')">▶ START</button>
    <button class="btn btn-red"   id="btnStop"  onclick="botControl('stop')">⏹ STOP</button>
  </div>
</header>

<div class="main">
  <!-- Portfolio Value Bar -->
  <div class="portfolio-bar" id="portfolioBar">
    <div class="pv-item"><div class="pv-label">Available Balance</div><div class="pv-val" id="pvAvail" style="color:var(--accent)">—</div></div>
    <div class="pv-div"></div>
    <div class="pv-item"><div class="pv-label">Wallet Balance</div><div class="pv-val" id="pvWallet" style="color:var(--text)">—</div></div>
    <div class="pv-div"></div>
    <div class="pv-item"><div class="pv-label">Open Positions Value</div><div class="pv-val" id="pvPositions" style="color:var(--yellow)">—</div></div>
    <div class="pv-div"></div>
    <div class="pv-item"><div class="pv-label">Total Portfolio Value</div><div class="pv-val" id="pvTotal" style="color:var(--green)">—</div></div>
    <div class="pv-div"></div>
    <div class="pv-item"><div class="pv-label">Unrealized P&L</div><div class="pv-val" id="pvUnreal">—</div></div>
  </div>

  <!-- Stat Cards -->
  <div class="stat-grid" id="sg"></div>

  <!-- Status Bar (last closed trade info) -->
  <div class="status-bar" id="lastTradeBar" style="display:none">
    <span style="color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.5px">Last Closed:</span>
    <div class="sb-item"><span class="sb-label">Coin</span><span id="sbCoin">—</span></div>
    <div class="sb-item"><span class="sb-label">Dir</span><span id="sbDir">—</span></div>
    <div class="sb-item"><span class="sb-label">Reason</span><span id="sbReason">—</span></div>
    <div class="sb-item"><span class="sb-label">Outcome</span><span id="sbOutcome">—</span></div>
    <div class="sb-item"><span class="sb-label">P&L</span><span id="sbPnl">—</span></div>
    <div class="sb-item"><span class="sb-label">Closed</span><span id="sbTime" style="color:var(--muted)">—</span></div>
  </div>

  <!-- TABS -->
  <div class="tabs">
    <div class="tab active" onclick="switchTab('open')"    id="tab-open">Open Trades</div>
    <div class="tab"        onclick="switchTab('orders')"  id="tab-orders">Open Orders</div>
    <div class="tab"        onclick="switchTab('closed')"  id="tab-closed">Closed Trades</div>
    <div class="tab"        onclick="switchTab('history')" id="tab-history">Trade History</div>
    <div class="tab"        onclick="switchTab('log')"     id="tab-log">Live Log</div>
    <div class="tab"        onclick="switchTab('errors')"  id="tab-errors">Errors <span class="badge" id="errBadge">0</span></div>
  </div>

  <!-- TAB: Open Trades -->
  <div class="tab-panel active" id="panel-open">
    <div class="panel">
      <div class="ph">
        Open Trades <span class="badge" id="oc">0</span>
        <div class="ph-right">
          <button class="btn-sm btn-close-sm" onclick="closeAllTrades()">✕ CLOSE ALL</button>
        </div>
      </div>
      <div class="tlist" id="ol"></div>
    </div>
  </div>

  <!-- TAB: Open Orders (Binance) -->
  <div class="tab-panel" id="panel-orders">
    <div class="panel">
      <div class="ph">
        Binance Open Orders <span class="badge" id="ooc">0</span>
        <div class="ph-right" style="color:var(--muted);font-size:10px">SL/TP pending on exchange</div>
      </div>
      <div style="overflow-x:auto">
        <table class="oo-table" id="ooTable">
          <thead>
            <tr>
              <th>Symbol</th><th>Type</th><th>Side</th><th>Trigger Price</th>
              <th>Qty / CloseAll</th><th>Status</th><th>Order ID</th><th>Time</th>
            </tr>
          </thead>
          <tbody id="ooBody"></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- TAB: Closed Trades -->
  <div class="tab-panel" id="panel-closed">
    <div class="panel">
      <div class="ph">Closed Trades <span class="badge" id="cc">0</span></div>
      <div class="tlist" id="cl"></div>
    </div>
  </div>

  <!-- TAB: Trade History -->
  <div class="tab-panel" id="panel-history">
    <div class="panel">
      <div class="ph">Trade History Log <span class="badge" id="hc">0</span></div>
      <div style="overflow-x:auto">
        <div style="display:grid;grid-template-columns:130px 80px 50px 95px 1fr 85px;gap:6px;padding:7px 14px;border-bottom:1px solid var(--border);font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px">
          <div>Time</div><div>Coin</div><div>Dir</div><div>P&L</div><div>Reason</div><div>Outcome</div>
        </div>
        <div class="tlist" id="histList" style="max-height:500px"></div>
      </div>
    </div>
  </div>

  <!-- TAB: Live Log -->
  <div class="tab-panel" id="panel-log">
    <div class="panel">
      <div class="ph">Live Log <span class="badge" id="sc">—</span></div>
      <div class="llist" id="ll" style="max-height:500px"></div>
    </div>
  </div>

  <!-- TAB: Errors -->
  <div class="tab-panel" id="panel-errors">
    <div class="panel">
      <div class="ph" style="color:var(--red)">System Errors <span class="badge" id="ec">0</span></div>
      <div class="elist" id="el" style="max-height:500px"></div>
    </div>
  </div>

</div><!-- end main -->

<div class="notify" id="notify"></div>

<script>
// ─── State ───
let _lastState = null;
let _lastHistLen = 0;
let _openOrdersCache = [];
let _activeTab = 'open';

// ─── Helpers ───
const f2=(n,d=2)=>typeof n==='number'?n.toFixed(d):'—';
const fu=n=>'$'+Math.abs(n).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g,',');
const fup=n=>(n>=0?'+':'')+fu(n);

function showNotify(msg,type='ok'){
  const el=document.getElementById('notify');
  el.textContent=msg;el.className='notify show '+type;
  setTimeout(()=>el.className='notify',3000);
}

function switchTab(name){
  _activeTab=name;
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p=>p.classList.remove('active'));
  document.getElementById('tab-'+name).classList.add('active');
  document.getElementById('panel-'+name).classList.add('active');
  // Refresh open orders when switching to that tab
  if(name==='orders') refreshOpenOrders();
}

// ─── Bot Controls ───
async function botControl(action){
  document.getElementById('btnStart').disabled=true;
  document.getElementById('btnStop').disabled=true;
  try{
    const r=await fetch('/api/bot/'+action,{method:'POST'});
    const d=await r.json();
    showNotify(d.msg||action+' sent',d.ok?'ok':'err');
  }catch(e){showNotify('Error: '+e,'err')}
  setTimeout(()=>{
    document.getElementById('btnStart').disabled=false;
    document.getElementById('btnStop').disabled=false;
  },1500);
  setTimeout(refresh,1000);
}

// ─── Trade Start/Stop Toggle ───
async function toggleTrading(){
  try{
    const r=await fetch('/api/bot/toggle_trading',{method:'POST'});
    const d=await r.json();
    showNotify(d.msg||'Toggled','info');
    updateToggle(d.trading_enabled);
  }catch(e){showNotify('Error: '+e,'err')}
}

function updateToggle(enabled){
  const track=document.getElementById('toggleTrack');
  const label=document.getElementById('toggleLabel');
  if(enabled){
    track.classList.add('on');
    label.textContent='TRADING ON';
    label.style.color='var(--green)';
  } else {
    track.classList.remove('on');
    label.textContent='TRADING OFF';
    label.style.color='var(--red)';
  }
}

// ─── Manual Scan ───
async function manualScan(){
  const btn=document.getElementById('btnScan');
  btn.disabled=true; btn.textContent='⏳ SCANNING...';
  try{
    const r=await fetch('/api/bot/manual_scan',{method:'POST'});
    const d=await r.json();
    showNotify(d.msg||'Scan triggered','info');
  }catch(e){showNotify('Error: '+e,'err')}
  setTimeout(()=>{btn.disabled=false;btn.textContent='⚡ SCAN';},3000);
  setTimeout(refresh,2000);
}

// ─── Close Trades ───
async function closeAllTrades(){
  const state=await (await fetch('/api/state')).json();
  const trades=state.open_trades||[];
  if(trades.length===0){showNotify('No open trades','err');return;}
  if(!confirm(`Close ALL ${trades.length} open trades at market price?`)) return;
  let ok=0,fail=0;
  for(const t of trades){
    try{
      const r=await fetch('/api/trade/close',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({trade_id:t.id})
      });
      const d=await r.json();
      if(d.ok) ok++; else fail++;
    }catch(e){fail++;}
    await new Promise(res=>setTimeout(res,400));
  }
  showNotify(`Closed ${ok} trades${fail?', '+fail+' failed':''}`,fail?'err':'ok');
  setTimeout(refresh,1500);
}

async function closeTrade(tradeId,sym){
  if(!confirm('Close '+sym+' at market price?')) return;
  const btn=document.getElementById('cb_'+tradeId);
  if(btn){btn.disabled=true;btn.textContent='...';}
  try{
    const r=await fetch('/api/trade/close',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({trade_id:tradeId})
    });
    const d=await r.json();
    showNotify(d.msg||'Done',d.ok?'ok':'err');
  }catch(e){showNotify('Error: '+e,'err')}
  setTimeout(refresh,1500);
}

// ─── Stat Cards ───
function renderStats(s){
  const pnl=s.total_pnl||0;
  const tot=(s.wins||0)+(s.losses||0)+(s.bes||0);
  const wr=tot>0?(s.wins/tot*100):0;
  const pc=pnl>=0?'var(--green)':'var(--red)';
  const avail=typeof s.live_balance==='number'?fu(s.live_balance):fu(s.balance||0);
  const wallet=typeof s.live_wallet==='number'?fu(s.live_wallet):fu(s.wallet_balance||0);
  const pending=s.pending_orders_count||0;
  const bpos=s.binance_position_count||0;
  document.getElementById('sg').innerHTML=`
    <div class="card a">
      <div class="clabel">Available</div>
      <div class="cval" style="color:var(--accent)">${avail}</div>
      <div class="csub">Wallet: ${wallet}</div>
    </div>
    <div class="card ${pnl>=0?'g':'r'}">
      <div class="clabel">Total P&L</div>
      <div class="cval" style="color:${pc}">${pnl>=0?'+':''}${fu(pnl)}</div>
      <div class="csub">Closed trades</div>
    </div>
    <div class="card g">
      <div class="clabel">Win Rate</div>
      <div class="cval" style="color:var(--green)">${f2(wr,1)}%</div>
      <div class="csub">✅${s.wins||0} 🟡${s.bes||0} ❌${s.losses||0}</div>
      <div class="pbar"><div class="pbfill" style="width:${wr}%"></div></div>
    </div>
    <div class="card y">
      <div class="clabel">Signals Today</div>
      <div class="cval" style="color:var(--yellow)">${s.signals_today||0}</div>
      <div class="csub">Unlimited</div>
    </div>
    <div class="card a">
      <div class="clabel">Open / Exchange</div>
      <div class="cval">${(s.open_trades||[]).length} / ${bpos}</div>
      <div class="csub">State / Binance</div>
    </div>
    <div class="card ${pending>0?'y':'g'}">
      <div class="clabel">Pending Orders</div>
      <div class="cval" style="color:${pending>0?'var(--yellow)':'var(--green)'}">${pending}</div>
      <div class="csub">SL/TP on exchange</div>
    </div>
    <div class="card g">
      <div class="clabel">Total Closed</div>
      <div class="cval">${(s.closed_trades||[]).length}</div>
      <div class="csub">All time</div>
    </div>`;
}

// ─── Portfolio Value ───
function renderPortfolio(s){
  const avail=typeof s.live_balance==='number'?s.live_balance:(s.balance||0);
  const wallet=typeof s.live_wallet==='number'?s.live_wallet:(s.wallet_balance||0);
  const openVal=s.open_positions_value||0;
  const unrealPnl=s.unrealized_pnl||0;
  const total=wallet+openVal;

  document.getElementById('pvAvail').textContent=fu(avail);
  document.getElementById('pvWallet').textContent=fu(wallet);
  document.getElementById('pvPositions').textContent=fu(openVal);
  document.getElementById('pvTotal').textContent=fu(total);
  const uEl=document.getElementById('pvUnreal');
  uEl.textContent=(unrealPnl>=0?'+':'')+fu(unrealPnl);
  uEl.style.color=unrealPnl>0?'var(--green)':unrealPnl<0?'var(--red)':'var(--yellow)';
}

// ─── Open Trade Row ───
function trowOpen(t,livePrices){
  const d=t.direction;
  const dc=d==='LONG'?'long':'short';
  const ar=d==='LONG'?'▲':'▼';
  const isSynced=t.order_id==='SYNCED';
  const syncTag=isSynced?'<span class="sync-tag">SYNC</span>':'';
  const tp1Tag=t.tp1_hit?'<span class="tp1-tag">✓TP1→BE</span>':'';
  const rs=(t.reasons||[]).slice(0,3).join(' | ');

  // Live P&L calculation
  const livePrice=livePrices[t.symbol];
  let livePnlHtml='—';
  let livePnlUsd=0;
  if(livePrice && t.entry){
    const ep=parseFloat(t.entry);
    const notional=parseFloat(t.notional||t.alloc_usd||0);
    if(d==='LONG') livePnlUsd=(livePrice-ep)/ep*notional;
    else livePnlUsd=(ep-livePrice)/ep*notional;
    const cls=livePnlUsd>0?'live-pnl-pos':livePnlUsd<0?'live-pnl-neg':'live-pnl-neu';
    livePnlHtml=`<span class="${cls}">${livePnlUsd>=0?'+':''}$${Math.abs(livePnlUsd).toFixed(2)}</span>`;
  }

  const slv=t.sl?t.sl.toFixed(4):'—';
  const tp1v=t.tp1?t.tp1.toFixed(4):'—';
  const tp2v=t.tp2?t.tp2.toFixed(4):'—';
  const livePriceStr=livePrice?`$${livePrice.toFixed(4)}`:'—';

  const levHtml=t.notional?`<span class="lev">×${t.leverage||1}=$${parseFloat(t.notional).toFixed(0)}</span>`:'';
  const openDt=t.open_time||'';

  return `<div class="trow trow-open open">
    <div>
      <div class="tcoin">${t.symbol.replace('USDT','')}${syncTag}${tp1Tag} <span style="font-size:9px;color:var(--muted)">sc:${t.score||'—'}</span></div>
      <div class="treason">${rs}</div>
      <div style="font-size:9px;color:var(--muted);margin-top:2px">
        <span style="color:var(--text)">Entry: $${parseFloat(t.entry).toFixed(4)}</span>
        <span style="color:var(--accent);margin-left:8px">Live: ${livePriceStr}</span>
      </div>
      <div style="font-size:9px;color:var(--muted);margin-top:2px">
        <span style="color:var(--red)">SL ${slv}</span> |
        <span style="color:var(--green)">TP1 ${tp1v}</span> |
        <span style="color:var(--accent)">TP2 ${tp2v}</span>
      </div>
      <div style="font-size:9px;color:var(--muted);margin-top:2px">📅 ${openDt}</div>
    </div>
    <div class="tdir ${dc}">${ar} ${d}</div>
    <div style="color:var(--accent);font-size:11px">${t.score||'—'}</div>
    <div style="font-size:11px">${livePnlHtml}${levHtml}</div>
    <div style="font-size:10px;color:var(--accent)">OPEN</div>
    <div><button class="btn-sm btn-close-sm" id="cb_${t.id}" onclick="closeTrade('${t.id}','${t.symbol}')">✕ CLOSE</button></div>
  </div>`;
}

// ─── Closed Trade Row ───
function trowClosed(t){
  const d=t.direction;
  const dc=d==='LONG'?'long':'short';
  const ar=d==='LONG'?'▲':'▼';
  const pnl=t.pnl_usd||0;
  const pc=pnl>0?'pos':pnl<0?'neg':'neu';
  const rc=t.outcome==='TP2_HIT'?'win':t.outcome==='SL_HIT'?'loss':'be';
  const rs=(t.reasons||[]).slice(0,2).join(' | ');
  const reason=t.close_reason||t.outcome||'—';
  const closeTime=(t.close_time||'').slice(11)||'';
  const openDt=t.open_time||'';

  return `<div class="trow trow-closed ${rc}">
    <div>
      <div class="tcoin">${t.symbol.replace('USDT','')}</div>
      <div class="treason">${rs}</div>
      <div style="font-size:9px;color:var(--muted);margin-top:2px">
        In: $${parseFloat(t.entry||0).toFixed(4)} → Out: $${parseFloat(t.exit_price||0).toFixed(4)}
      </div>
      <div style="font-size:9px;color:var(--muted)">📅 ${openDt}</div>
    </div>
    <div class="tdir ${dc}">${ar} ${d}</div>
    <div style="color:var(--accent);font-size:11px">${t.score||'—'}</div>
    <div><span class="tpnl ${pc}">${pnl>=0?'+':''}$${Math.abs(pnl).toFixed(2)}</span></div>
    <div style="font-size:9px;color:var(--muted)">${reason}</div>
    <div style="font-size:9px;color:var(--muted)">${closeTime}</div>
  </div>`;
}

// ─── Render Trades ───
function renderTrades(s,livePrices){
  const open=s.open_trades||[];
  const closed=s.closed_trades||[];
  document.getElementById('oc').textContent=open.length;
  document.getElementById('cc').textContent=closed.length;
  document.getElementById('ol').innerHTML=open.length
    ?open.map(t=>trowOpen(t,livePrices)).join('')
    :'<div class="empty">No open trades</div>';
  document.getElementById('cl').innerHTML=closed.length
    ?closed.slice(0,80).map(t=>trowClosed(t)).join('')
    :'<div class="empty">No closed trades yet</div>';
}

// ─── Open Orders Tab ───
function renderOpenOrders(orders){
  document.getElementById('ooc').textContent=orders.length;
  const tbody=document.getElementById('ooBody');
  if(!orders.length){
    tbody.innerHTML=`<tr><td colspan="8" style="text-align:center;padding:24px;color:var(--muted)">No open orders on exchange</td></tr>`;
    return;
  }
  tbody.innerHTML=orders.map(o=>{
    const t=o.type||'';
    let typeCls='oo-type-other', typeLabel=t;
    if(t.includes('STOP')){typeCls='oo-type-sl';typeLabel='🛑 '+t;}
    else if(t.includes('TAKE_PROFIT')){typeCls='oo-type-tp';typeLabel='🎯 '+t;}
    const side=o.side||'—';
    const sideCls=side==='BUY'?'style="color:var(--green)"':'style="color:var(--red)"';
    const price=o.stopPrice||o.price||o.triggerPrice||'—';
    const qty=o.origQty||'—';
    const closeAll=o.closePosition==='true'||o.closePosition===true?'YES':'—';
    const status=o.status||'—';
    const oid=o.orderId||o.algoId||'—';
    const ts=o.updateTime?new Date(o.updateTime).toLocaleTimeString():'—';
    return `<tr>
      <td style="font-weight:700">${o.symbol||'—'}</td>
      <td class="${typeCls}">${typeLabel}</td>
      <td ${sideCls}>${side}</td>
      <td>$${parseFloat(price||0).toFixed(4)}</td>
      <td>${qty} ${closeAll!=='—'?'<span style="font-size:9px;color:var(--yellow)">(CloseAll)</span>':''}</td>
      <td style="color:var(--muted)">${status}</td>
      <td style="font-size:10px;color:var(--muted)">${oid}</td>
      <td style="font-size:10px;color:var(--muted)">${ts}</td>
    </tr>`;
  }).join('');
}

async function refreshOpenOrders(){
  try{
    const r=await fetch('/api/open_orders');
    const d=await r.json();
    _openOrdersCache=d.orders||[];
    renderOpenOrders(_openOrdersCache);
  }catch(e){console.error('Open orders fetch error:',e)}
}

// ─── Trade History ───
function renderHistory(s){
  const hist=s.trade_history||[];
  document.getElementById('hc').textContent=hist.length;

  // Show/update status bar with latest closed trade
  if(hist.length>0){
    const latest=hist[0];
    document.getElementById('lastTradeBar').style.display='flex';
    document.getElementById('sbCoin').textContent=latest.symbol||'—';
    document.getElementById('sbDir').textContent=latest.direction||'—';
    document.getElementById('sbDir').style.color=latest.direction==='LONG'?'var(--green)':'var(--red)';
    document.getElementById('sbReason').textContent=latest.reason||'—';
    const outEl=document.getElementById('sbOutcome');
    outEl.textContent=latest.outcome||'—';
    outEl.style.color=latest.outcome==='WIN'?'var(--green)':latest.outcome==='LOSS'?'var(--red)':'var(--yellow)';
    const pnl=latest.pnl_usd||0;
    const pnlEl=document.getElementById('sbPnl');
    pnlEl.textContent=(pnl>=0?'+':'')+'$'+Math.abs(pnl).toFixed(2);
    pnlEl.style.color=pnl>0?'var(--green)':pnl<0?'var(--red)':'var(--yellow)';
    document.getElementById('sbTime').textContent=(latest.close_time||'').slice(11)||'—';

    // Flash status bar if new entry
    if(hist.length!==_lastHistLen){
      document.getElementById('lastTradeBar').style.boxShadow='0 0 12px rgba(0,229,255,.4)';
      setTimeout(()=>document.getElementById('lastTradeBar').style.boxShadow='',2000);
      _lastHistLen=hist.length;
    }
  }

  document.getElementById('histList').innerHTML=hist.slice(0,200).map(h=>{
    const pnl=h.pnl_usd||0;
    const pnlCls=pnl>0?'live-pnl-pos':pnl<0?'live-pnl-neg':'live-pnl-neu';
    const outCls=h.outcome||'';
    return `<div class="hist-row">
      <div style="color:var(--muted)">${h.close_time||'—'}</div>
      <div style="font-weight:700">${(h.symbol||'').replace('USDT','')}</div>
      <div class="${h.direction==='LONG'?'tdir long':'tdir short'}">${h.direction==='LONG'?'▲ L':'▼ S'}</div>
      <div class="${pnlCls}">${pnl>=0?'+':''}$${Math.abs(pnl).toFixed(2)}</div>
      <div style="color:var(--muted);font-size:10px">${h.reason||'—'}</div>
      <div class="hist-outcome ${outCls}">${h.outcome||'—'}</div>
    </div>`;
  }).join('')||'<div class="empty">No history yet</div>';
}

// ─── Log & Errors ───
function renderLog(s){
  document.getElementById('sc').textContent='Scan: '+(s.last_scan||'—');
  document.getElementById('ll').innerHTML=(s.log||[]).slice(0,100).map(l=>
    `<div class="le"><span class="lts">${l.ts}</span><span class="lm ${l.level||''}">${l.msg}</span></div>`
  ).join('')||'<div class="empty">No logs</div>';
}

function renderErrors(s){
  const e=s.errors||[];
  document.getElementById('ec').textContent=e.length;
  document.getElementById('errBadge').textContent=e.length;
  document.getElementById('errBadge').style.color=e.length>0?'var(--red)':'var(--text)';
  document.getElementById('el').innerHTML=e.slice(0,50).map(x=>
    `<div class="ee">${x.ts} — ${x.msg}</div>`
  ).join('')||'<div class="empty" style="color:var(--green)">✅ No errors</div>';
}

// ─── Status Bar ───
function renderStatus(s){
  const st=s.bot_status||'offline';
  const running=s.bot_running!==false;
  const tradingEnabled=s.trading_enabled!==false;
  document.getElementById('sdot').className='dot '+st;
  document.getElementById('stxt').textContent=st.toUpperCase();
  document.getElementById('upd').textContent=new Date().toLocaleTimeString();
  document.getElementById('btnStart').disabled=running;
  document.getElementById('btnStop').disabled=!running;
  updateToggle(tradingEnabled);
}

// ─── Main Refresh ───
async function refresh(){
  try{
    const r=await fetch('/api/state');
    const s=await r.json();
    _lastState=s;
    const livePrices=s.live_prices||{};
    renderStats(s);
    renderPortfolio(s);
    renderTrades(s,livePrices);
    renderHistory(s);
    renderLog(s);
    renderErrors(s);
    renderStatus(s);
    if(_activeTab==='orders') renderOpenOrders(s.open_orders||_openOrdersCache);
  }catch(e){console.error('Refresh error:',e)}
}

refresh();
setInterval(refresh,5000);
// Refresh open orders every 15s when on that tab
setInterval(()=>{ if(_activeTab==='orders') refreshOpenOrders(); },15000);
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

    # Balance only fetched when bot processes a signal (not on every dashboard poll)
    # But we still show last known values from state
    avail  = state.get("balance", 0)
    wallet = state.get("wallet_balance", 0)
    state["live_balance"] = avail
    state["live_wallet"]  = wallet

    # Pending orders count (lightweight)
    orders = get_pending_orders()
    state["pending_orders_count"] = len(orders)
    state["open_orders"] = orders  # include in state for tab

    # Binance position count
    bpos = get_binance_positions()
    state["binance_position_count"] = len(bpos)

    # Live prices for open trades (unrealized P&L)
    open_syms = list(set(t["symbol"] for t in state.get("open_trades", [])))
    live_prices = {}
    open_positions_value = 0.0
    unrealized_pnl = 0.0

    if open_syms:
        live_prices = get_live_prices(open_syms)
        for t in state.get("open_trades", []):
            sym = t["symbol"]
            p = live_prices.get(sym)
            if p and t.get("entry"):
                entry = float(t["entry"])
                notional = float(t.get("notional") or t.get("alloc_usd") or 0)
                if t["direction"] == "LONG":
                    upnl = (p - entry) / entry * notional if entry > 0 else 0
                else:
                    upnl = (entry - p) / entry * notional if entry > 0 else 0
                open_positions_value += notional + upnl
                unrealized_pnl += upnl

    state["live_prices"] = live_prices
    state["open_positions_value"] = round(open_positions_value, 2)
    state["unrealized_pnl"] = round(unrealized_pnl, 2)

    return jsonify(state)

@app.route("/api/open_orders")
def api_open_orders():
    orders = get_pending_orders()
    return jsonify({"orders": orders, "count": len(orders)})

@app.route("/api/bot/start", methods=["POST"])
def bot_start():
    state = load_state()
    state["bot_running"] = True
    state["bot_status"]  = "running"
    state["log"].insert(0, {"ts": ts_sl(), "msg": "▶ Bot STARTED via dashboard", "level": "INFO"})
    state["log"] = state["log"][:300]
    save_state(state)
    return jsonify({"ok": True, "msg": "Bot started"})

@app.route("/api/bot/stop", methods=["POST"])
def bot_stop():
    state = load_state()
    state["bot_running"] = False
    state["bot_status"]  = "stopped"
    state["log"].insert(0, {"ts": ts_sl(), "msg": "⏹ Bot STOPPED via dashboard", "level": "INFO"})
    state["log"] = state["log"][:300]
    save_state(state)
    return jsonify({"ok": True, "msg": "Bot stopped"})

@app.route("/api/bot/toggle_trading", methods=["POST"])
def bot_toggle_trading():
    """Toggle the trading_enabled flag. When OFF, bot ignores all signals."""
    state = load_state()
    current = state.get("trading_enabled", True)
    new_val  = not current
    state["trading_enabled"] = new_val
    msg = "Trading ENABLED" if new_val else "Trading DISABLED — signals will be ignored"
    state["log"].insert(0, {"ts": ts_sl(), "msg": f"{'▶' if new_val else '⏸'} {msg} via dashboard", "level": "INFO"})
    state["log"] = state["log"][:300]
    save_state(state)
    return jsonify({"ok": True, "msg": msg, "trading_enabled": new_val})

@app.route("/api/bot/manual_scan", methods=["POST"])
def bot_manual_scan():
    """Signal the bot engine to run a scan immediately."""
    state = load_state()
    if not state.get("bot_running", True):
        return jsonify({"ok": False, "msg": "Bot is stopped — start it first"})
    if not state.get("trading_enabled", True):
        return jsonify({"ok": False, "msg": "Trading is disabled — enable trading first"})
    state["manual_scan_requested"] = True
    state["log"].insert(0, {"ts": ts_sl(), "msg": "⚡ Manual scan requested via dashboard", "level": "INFO"})
    state["log"] = state["log"][:300]
    save_state(state)
    return jsonify({"ok": True, "msg": "Manual scan queued — bot will run next cycle"})

@app.route("/api/trade/close", methods=["POST"])
def api_close_trade():
    data     = request.get_json(silent=True) or {}
    trade_id = data.get("trade_id", "")

    state = load_state()
    trade = next((t for t in state["open_trades"] if t["id"] == trade_id), None)
    if not trade:
        return jsonify({"ok": False, "msg": "Trade not found in state"})

    symbol     = trade["symbol"]
    direction  = trade["direction"]
    qty        = trade.get("qty", 0)
    close_side = "SELL" if direction == "LONG" else "BUY"

    # Cancel SL/TP orders (algo orders)
    for oid in [trade.get("sl_order_id"), trade.get("tp_order_id"), trade.get("tp2_order_id")]:
        if oid:
            _bdel_algo(oid)
            time.sleep(0.2)

    # Get qty from Binance if needed
    if qty <= 0:
        positions = get_binance_positions()
        for p in positions:
            if p["symbol"] == symbol:
                amt = abs(float(p.get("positionAmt", 0)))
                if amt > 0:
                    qty = amt
                    break

    if qty <= 0:
        return jsonify({"ok": False, "msg": f"Cannot determine qty for {symbol}"})

    # Market close
    resp = _bpost("/fapi/v1/order", {
        "symbol":       symbol,
        "side":         close_side,
        "type":         "MARKET",
        "quantity":     qty,
        "positionSide": "BOTH",
    })

    if not isinstance(resp, dict) or "orderId" not in resp:
        return jsonify({"ok": False, "msg": f"Close order failed: {resp}"})

    # Exit price
    price_data = _bget("/fapi/v1/ticker/price", {"symbol": symbol})
    exit_price = float(price_data["price"]) if price_data and "price" in price_data else trade["entry"]

    entry    = trade["entry"]
    notional = trade.get("notional", trade.get("alloc_usd", 0))
    if direction == "LONG":
        pnl_pct = (exit_price - entry) / entry * 100 if entry > 0 else 0
    else:
        pnl_pct = (entry - exit_price) / entry * 100 if entry > 0 else 0
    pnl_usd = round(notional * pnl_pct / 100, 2)

    state = load_state()
    state["total_pnl"] = round(state.get("total_pnl", 0) + pnl_usd, 2)
    if pnl_usd > 0:   state["wins"]   += 1
    elif pnl_usd < 0: state["losses"] += 1
    else:              state["bes"]    += 1

    trade["outcome"]      = "MANUAL_CLOSE"
    trade["close_reason"] = "Manual Close"
    trade["pnl_usd"]      = pnl_usd
    trade["pnl_pct"]      = round(pnl_pct, 3)
    trade["close_time"]   = dt_sl()
    trade["exit_price"]   = exit_price

    # Add to trade_history
    if "trade_history" not in state:
        state["trade_history"] = []
    hist_entry = {
        "id":         trade.get("id", ""),
        "symbol":     symbol,
        "direction":  direction,
        "entry":      entry,
        "exit_price": exit_price,
        "open_time":  trade.get("open_time", ""),
        "close_time": dt_sl(),
        "reason":     "Manual Close",
        "outcome":    "WIN" if pnl_usd > 0 else "LOSS" if pnl_usd < 0 else "BREAKEVEN",
        "pnl_usd":    pnl_usd,
        "score":      trade.get("score", 0),
        "leverage":   trade.get("leverage", LEVERAGE),
    }
    state["trade_history"].insert(0, hist_entry)
    state["trade_history"] = state["trade_history"][:300]

    state["closed_trades"].insert(0, trade)
    state["closed_trades"] = state["closed_trades"][:500]
    state["open_trades"]   = [t for t in state["open_trades"] if t["id"] != trade_id]

    em = "✅" if pnl_usd >= 0 else "❌"
    state["log"].insert(0, {"ts": ts_sl(), "msg": f"{em} MANUAL CLOSE {symbol} {direction} @ ${exit_price:.4f} | P&L: ${pnl_usd:+.2f}", "level": "INFO"})
    state["log"] = state["log"][:300]
    save_state(state)

    return jsonify({"ok": True, "msg": f"Closed {symbol} — P&L: ${pnl_usd:+.2f}"})

@app.route("/api/health")
def health():
    from datetime import datetime
    return jsonify({"status": "ok", "time": datetime.now(SL_TZ).isoformat()})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
