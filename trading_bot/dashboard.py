import os, json
from flask import Flask, jsonify, render_template_string
from datetime import datetime

STATE_FILE = os.environ.get("STATE_FILE", "/tmp/bot_state.json")
STARTING_BALANCE = 100.0

app = Flask(__name__)

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
            "bot_status": "offline",
            "last_scan": "",
            "wins": 0, "losses": 0, "bes": 0,
            "total_pnl": 0.0,
            "signals_today": 0,
            "log": []
        }

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Paper Trading Bot</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@700;800&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #080c14;
    --surface: #0e1520;
    --border: #1a2540;
    --accent: #00e5ff;
    --green: #00e676;
    --red: #ff1744;
    --yellow: #ffd600;
    --text: #c8d8f0;
    --muted: #4a6080;
    --win: #00e676;
    --loss: #ff1744;
    --be: #ffd600;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Space Mono', monospace;
    min-height: 100vh;
    font-size: 13px;
  }

  /* Header */
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 18px 28px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
    position: sticky; top: 0; z-index: 100;
  }
  .logo {
    font-family: 'Syne', sans-serif;
    font-size: 20px;
    font-weight: 800;
    letter-spacing: -0.5px;
    color: var(--accent);
  }
  .logo span { color: var(--green); }
  .status-pill {
    display: flex; align-items: center; gap: 8px;
    padding: 6px 14px;
    border-radius: 20px;
    border: 1px solid var(--border);
    font-size: 11px;
    background: var(--bg);
  }
  .dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--muted);
  }
  .dot.running { background: var(--green); box-shadow: 0 0 8px var(--green); animation: pulse 2s infinite; }
  .dot.scanning { background: var(--accent); box-shadow: 0 0 8px var(--accent); animation: pulse 0.8s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }

  .last-update { color: var(--muted); font-size: 10px; }

  /* Grid */
  .main { padding: 20px 24px; }

  .stat-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px;
    margin-bottom: 20px;
  }
  .stat-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
    position: relative;
    overflow: hidden;
  }
  .stat-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
  }
  .stat-card.accent::before { background: var(--accent); }
  .stat-card.green::before  { background: var(--green); }
  .stat-card.red::before    { background: var(--red); }
  .stat-card.yellow::before { background: var(--yellow); }

  .stat-label { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px; }
  .stat-value { font-family: 'Syne', sans-serif; font-size: 22px; font-weight: 800; }
  .stat-sub   { color: var(--muted); font-size: 10px; margin-top: 4px; }

  /* Panels */
  .panels {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-bottom: 16px;
  }
  @media (max-width: 900px) { .panels { grid-template-columns: 1fr; } }

  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
  }
  .panel-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 16px;
    border-bottom: 1px solid var(--border);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--accent);
    font-weight: 700;
  }
  .badge {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 2px 8px;
    font-size: 10px;
    color: var(--text);
  }

  /* Trade rows */
  .trade-list { max-height: 380px; overflow-y: auto; }
  .trade-row {
    display: grid;
    grid-template-columns: 1fr 80px 70px 90px 70px;
    gap: 8px;
    padding: 10px 16px;
    border-bottom: 1px solid var(--border);
    align-items: center;
    transition: background 0.15s;
  }
  .trade-row:hover { background: rgba(0, 229, 255, 0.03); }
  .trade-row.win  { border-left: 2px solid var(--green); }
  .trade-row.loss { border-left: 2px solid var(--red); }
  .trade-row.be   { border-left: 2px solid var(--yellow); }
  .trade-row.open { border-left: 2px solid var(--accent); }

  .trade-coin { font-weight: 700; font-size: 12px; }
  .trade-dir  { font-size: 10px; }
  .dir-long  { color: var(--green); }
  .dir-short { color: var(--red); }
  .trade-score { color: var(--accent); }
  .trade-pnl.pos { color: var(--green); }
  .trade-pnl.neg { color: var(--red); }
  .trade-pnl.neu { color: var(--yellow); }
  .trade-outcome { font-size: 10px; }

  .reasons { font-size: 9px; color: var(--muted); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  /* Log */
  .log-panel { grid-column: 1 / -1; }
  .log-list { max-height: 200px; overflow-y: auto; font-size: 11px; }
  .log-entry {
    padding: 5px 16px;
    border-bottom: 1px solid var(--border);
    display: flex;
    gap: 12px;
    align-items: baseline;
  }
  .log-ts { color: var(--muted); font-size: 10px; flex-shrink: 0; }
  .log-msg.WARN  { color: var(--yellow); }
  .log-msg.ERROR { color: var(--red); }

  /* Empty */
  .empty { padding: 28px; text-align: center; color: var(--muted); font-size: 11px; }

  /* Progress bar */
  .progress-wrap { background: var(--bg); border-radius: 4px; overflow: hidden; height: 4px; margin-top: 8px; }
  .progress-bar  { height: 100%; border-radius: 4px; background: var(--green); transition: width 0.5s; }

  /* Win rate ring */
  .ring-wrap { display: flex; align-items: center; gap: 12px; margin-top: 4px; }
  .ring-label { font-size: 11px; color: var(--muted); }
  .ring-val   { font-family: 'Syne', sans-serif; font-size: 18px; font-weight: 800; }

  scrollbar-width: thin;
  ::-webkit-scrollbar { width: 4px; }
  ::-webkit-scrollbar-track { background: var(--bg); }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
</style>
</head>
<body>

<header>
  <div class="logo">PAPER<span>BOT</span></div>
  <div style="display:flex;gap:12px;align-items:center;">
    <span class="last-update" id="lastUpdate">—</span>
    <div class="status-pill">
      <div class="dot" id="statusDot"></div>
      <span id="statusText">—</span>
    </div>
  </div>
</header>

<div class="main">

  <div class="stat-grid" id="statsGrid">
    <!-- Filled by JS -->
  </div>

  <div class="panels">

    <div class="panel">
      <div class="panel-header">
        Open Trades
        <span class="badge" id="openCount">0</span>
      </div>
      <div class="trade-list" id="openList"></div>
    </div>

    <div class="panel">
      <div class="panel-header">
        Closed Trades
        <span class="badge" id="closedCount">0</span>
      </div>
      <div class="trade-list" id="closedList"></div>
    </div>

    <div class="panel log-panel">
      <div class="panel-header">
        Live Log
        <span class="badge" id="scanTime">—</span>
      </div>
      <div class="log-list" id="logList"></div>
    </div>

  </div>
</div>

<script>
const fmt = (n, d=2) => typeof n === 'number' ? n.toFixed(d) : '—';
const fmtUSD = n => '$' + fmt(Math.abs(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ',');

function renderStats(s) {
  const pnl   = (s.balance - s.starting);
  const pnlPct= (pnl / s.starting * 100);
  const total = s.wins + s.losses + s.bes;
  const wr    = total > 0 ? (s.wins / total * 100) : 0;
  const roiColor = pnl >= 0 ? 'var(--green)' : 'var(--red)';

  document.getElementById('statsGrid').innerHTML = `
    <div class="stat-card accent">
      <div class="stat-label">Balance</div>
      <div class="stat-value" style="color:var(--accent)">${fmtUSD(s.balance)}</div>
      <div class="stat-sub">Started: ${fmtUSD(s.starting)}</div>
    </div>
    <div class="stat-card ${pnl>=0?'green':'red'}">
      <div class="stat-label">Total P&amp;L</div>
      <div class="stat-value" style="color:${roiColor}">${pnl>=0?'+':''}${fmtUSD(pnl)}</div>
      <div class="stat-sub">${pnlPct>=0?'+':''}${fmt(pnlPct)}% ROI</div>
    </div>
    <div class="stat-card green">
      <div class="stat-label">Win Rate</div>
      <div class="stat-value" style="color:var(--green)">${fmt(wr, 1)}%</div>
      <div class="stat-sub">✅${s.wins} 🟡${s.bes} ❌${s.losses}</div>
      <div class="progress-wrap"><div class="progress-bar" style="width:${wr}%"></div></div>
    </div>
    <div class="stat-card yellow">
      <div class="stat-label">Today's Signals</div>
      <div class="stat-value" style="color:var(--yellow)">${s.signals_today || 0}</div>
      <div class="stat-sub">Max ${15} / day</div>
    </div>
    <div class="stat-card accent">
      <div class="stat-label">Open Trades</div>
      <div class="stat-value">${s.open_trades.length}</div>
      <div class="stat-sub">Max ${8} concurrent</div>
    </div>
    <div class="stat-card green">
      <div class="stat-label">Total Trades</div>
      <div class="stat-value">${total}</div>
      <div class="stat-sub">Closed: ${s.closed_trades.length}</div>
    </div>
  `;
}

function tradeRow(t, isOpen) {
  const dir   = t.direction;
  const dCls  = dir === 'LONG' ? 'dir-long' : 'dir-short';
  const arrow = dir === 'LONG' ? '▲' : '▼';
  const pnl   = t.pnl_usd || 0;
  const pnlCls= pnl > 0 ? 'pos' : pnl < 0 ? 'neg' : 'neu';
  let rowCls  = 'open';
  if (!isOpen) {
    rowCls = t.outcome === 'TP2_HIT' ? 'win' : t.outcome === 'SL_HIT' ? 'loss' : 'be';
  }
  const reasons = (t.reasons || []).slice(0, 3).join(' | ');
  const pnlStr  = isOpen ? '—' : `<span class="trade-pnl ${pnlCls}">${pnl>=0?'+':''}$${Math.abs(pnl).toFixed(2)}</span>`;
  const outcome = isOpen ? '<span style="color:var(--accent)">OPEN</span>' : t.outcome;

  return `<div class="trade-row ${rowCls}">
    <div>
      <div class="trade-coin">${t.symbol.replace('USDT','')}</div>
      <div class="reasons">${reasons}</div>
    </div>
    <div class="trade-dir ${dCls}">${arrow} ${dir}</div>
    <div class="trade-score">${t.score}/10</div>
    <div>${pnlStr}</div>
    <div class="trade-outcome">${outcome}</div>
  </div>`;
}

function renderTrades(s) {
  const openEl   = document.getElementById('openList');
  const closedEl = document.getElementById('closedList');
  document.getElementById('openCount').textContent   = s.open_trades.length;
  document.getElementById('closedCount').textContent = s.closed_trades.length;

  openEl.innerHTML   = s.open_trades.length   ? s.open_trades.map(t  => tradeRow(t, true)).join('')
                                               : '<div class="empty">No open trades</div>';
  closedEl.innerHTML = s.closed_trades.length ? s.closed_trades.slice(0,50).map(t => tradeRow(t, false)).join('')
                                              : '<div class="empty">No closed trades yet</div>';
}

function renderLog(s) {
  const el = document.getElementById('logList');
  if (!s.log || !s.log.length) { el.innerHTML = '<div class="empty">No logs yet</div>'; return; }
  el.innerHTML = s.log.slice(0, 50).map(l =>
    `<div class="log-entry">
      <span class="log-ts">${l.ts}</span>
      <span class="log-msg ${l.level}">${l.msg}</span>
    </div>`).join('');
}

function updateStatus(s) {
  const dot  = document.getElementById('statusDot');
  const text = document.getElementById('statusText');
  const st   = s.bot_status || 'offline';
  dot.className  = 'dot ' + st;
  text.textContent = st.toUpperCase();
  document.getElementById('scanTime').textContent = 'Scan: ' + (s.last_scan || '—');
  document.getElementById('lastUpdate').textContent = new Date().toLocaleTimeString();
}

async function refresh() {
  try {
    const r = await fetch('/api/state');
    const s = await r.json();
    renderStats(s);
    renderTrades(s);
    renderLog(s);
    updateStatus(s);
  } catch(e) { console.error(e); }
}

refresh();
setInterval(refresh, 5000);  // refresh every 5 seconds
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
