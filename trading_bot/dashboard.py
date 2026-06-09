import os, json
from flask import Flask, jsonify, render_template_string
from datetime import datetime

STATE_FILE = os.environ.get("STATE_FILE", "/tmp/bot_state.json")
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
            "total_pnl": 0.0, "signals_today": 0,
            "log": [], "errors": []
        }

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
  .pill{display:flex;align-items:center;gap:8px;padding:5px 12px;border-radius:20px;border:1px solid var(--border);font-size:11px;background:var(--bg)}
  .dot{width:8px;height:8px;border-radius:50%;background:var(--muted)}
  .dot.idle{background:var(--green);box-shadow:0 0 6px var(--green)}
  .dot.scanning{background:var(--accent);box-shadow:0 0 6px var(--accent);animation:pulse .8s infinite}
  .dot.running{background:var(--green);box-shadow:0 0 6px var(--green);animation:pulse 2s infinite}
  .dot.error{background:var(--red);box-shadow:0 0 6px var(--red)}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
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
  .tlist{max-height:400px;overflow-y:auto}
  .trow{padding:10px 14px;border-bottom:1px solid var(--border);display:grid;grid-template-columns:1fr 80px 65px 90px 75px;gap:6px;align-items:center}
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
  .lc1{grid-column:1/-1}
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
</style>
</head>
<body>
<header>
  <div class="logo">FUTURES<span>BOT</span></div>
  <div style="display:flex;gap:10px;align-items:center">
    <span style="color:var(--muted);font-size:10px" id="upd">—</span>
    <div class="pill"><div class="dot" id="sdot"></div><span id="stxt">—</span></div>
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
<script>
const f2=(n,d=2)=>typeof n==='number'?n.toFixed(d):'—';
const fu=n=>'$'+f2(Math.abs(n)).replace(/\\B(?=(\\d{3})+(?!\\d))/g,',');

function stats(s){
  const pnl=s.total_pnl||0;
  const tot=s.wins+s.losses+s.bes;
  const wr=tot>0?(s.wins/tot*100):0;
  const pc=pnl>=0?'var(--green)':'var(--red)';
  document.getElementById('sg').innerHTML=`
    <div class="card a">
      <div class="clabel">Available</div>
      <div class="cval" style="color:var(--accent)">${fu(s.balance)}</div>
      <div class="csub">Wallet: ${fu(s.wallet_balance||0)}</div>
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
  const notional=t.notional?`<br><span class="lev">×${t.leverage||5} = $${t.notional.toFixed(0)}</span>`:'';
  return `<div class="trow ${rc}">
    <div><div class="tcoin">${t.symbol.replace('USDT','')}</div><div class="treason">${rs}</div></div>
    <div class="tdir ${dc}">${ar} ${d}</div>
    <div style="color:var(--accent)">${t.score}/10</div>
    <div>${pstr}${notional}</div>
    <div style="font-size:10px">${out}</div>
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
  document.getElementById('sdot').className='dot '+st;
  document.getElementById('stxt').textContent=st.toUpperCase();
  document.getElementById('sc').textContent='Scan: '+(s.last_scan||'—');
  document.getElementById('upd').textContent=new Date().toLocaleTimeString();
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

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/state")
def api_state():
    return jsonify(load_state())

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "time": datetime.utcnow().isoformat()})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
