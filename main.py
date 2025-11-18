#!/usr/bin/env python3 
# -*- coding: utf-8 -*-
"""
main.py — Trading UI with robust rendering & SSE
- HTML wrapped in {% raw %} ... {% endraw %} to prevent Jinja collisions
- SSE newlines fixed
- /status hardened against broker errors
"""

import os
import json
import time
import queue
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

from flask import Flask, render_template_string, jsonify, request, Response
from dotenv import load_dotenv

load_dotenv()

# Local modules
from trading_crew import TradingCrew
import logging

# These imports may fail when tokens are missing; we’ll guard their usage
try:
    from upstox_operator import UpstoxOperator
except Exception:
    UpstoxOperator = None

try:
    from upstox_technical import UpstoxTechnicalClient
except Exception:
    UpstoxTechnicalClient = None

try:
    from news_client import NewsClient
except Exception:
    NewsClient = None

IST = ZoneInfo("Asia/Kolkata")
DATA_DIR = Path("./data"); DATA_DIR.mkdir(exist_ok=True)

# Max number of different symbols we’ll trade per cycle (NOT a restriction by name)
MAX_DISCOVERED_SYMBOLS = int(os.environ.get("MAX_DISCOVERED_SYMBOLS", "20"))

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'trading-secret-2024')

logging.getLogger("werkzeug").setLevel(logging.ERROR)
trading_active = False
status_queue = queue.Queue()

HTML = r"""{% raw %}
<!DOCTYPE html>
<html>
<head>
    <title>AI Trading System</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        .card { background: white; border-radius: 15px; padding: 25px; margin-bottom: 20px; box-shadow: 0 10px 30px rgba(0,0,0,0.2); }
        h1 { font-size: 32px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 20px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 20px; }
        .metric { background: linear-gradient(135deg, #667eea15 0%, #764ba215 100%); padding: 20px; border-radius: 10px; text-align: center; }
        .metric-label { font-size: 14px; color: #666; margin-bottom: 10px; }
        .metric-value { font-size: 28px; font-weight: bold; color: #333; }
        .status-badge { display: inline-block; padding: 8px 16px; border-radius: 20px; font-size: 14px; font-weight: 600; }
        .status-open { background: #10b981; color: white; }
        .status-closed { background: #ef4444; color: white; }
        .status-running { background: #3b82f6; color: white; animation: pulse 2s infinite; }
        .status-idle { background: #6b7280; color: white; }
        .controls { display: flex; gap: 15px; margin-top: 20px; flex-wrap: wrap; }
        .form-group { flex: 1; min-width: 200px; }
        label { display: block; font-size: 14px; color: #666; margin-bottom: 8px; font-weight: 500; }
        input, select { width: 100%; padding: 12px; border: 2px solid #e0e0e0; border-radius: 8px; font-size: 14px; }
        input:focus, select:focus { outline: none; border-color: #667eea; }
        button { padding: 14px 28px; border: none; border-radius: 8px; font-size: 15px; font-weight: 600; cursor: pointer; transition: all 0.3s; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        button:hover { transform: translateY(-2px); box-shadow: 0 6px 12px rgba(0,0,0,0.15); }
        button:disabled { opacity: 0.5; cursor: not-allowed; transform: none !important; }
        .btn-success { background: linear-gradient(135deg, #10b981 0%, #059669 100%); color: white; }
        .btn-danger { background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%); color: white; }
        .btn-primary { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }
        .activity-feed { background: #f9fafb; border-radius: 10px; padding: 20px; max-height: 500px; overflow-y: auto; font-family: 'Courier New', monospace; font-size: 13px; }
        .activity-item { padding: 12px; margin-bottom: 10px; border-radius: 6px; border-left: 4px solid #667eea; background: white; }
        .activity-time { color: #999; font-size: 11px; }
        .activity-text { color: #333; margin-top: 5px; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.6; } }
        .positive { color: #10b981; }
        .negative { color: #ef4444; }
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <h1>🤖 AI Trading System</h1>
            <div style="color: #666; margin-bottom: 20px;">Agent-Based Discovery • Multi-Signal Analysis • Risk Managed</div>
        </div>

        <div class="grid">
            <div class="card">
                <div class="metric">
                    <div class="metric-label">Market Status</div>
                    <span id="market-status" class="status-badge status-closed">Loading...</span>
                    <div style="margin-top: 10px; font-size: 14px; color: #666;">
                        <div id="market-time">--:--:--</div>
                    </div>
                </div>
            </div>

            <div class="card">
                <div class="metric">
                    <div class="metric-label">Available Funds</div>
                    <div id="available-margin" class="metric-value">₹0</div>
                </div>
            </div>

            <div class="card">
                <div class="metric">
                    <div class="metric-label">Trading Status</div>
                    <span id="trading-status" class="status-badge status-idle">Idle</span>
                </div>
            </div>
        </div>

        <div class="card">
            <h2 style="margin-bottom: 20px; color: #333;">Trading Controls</h2>
            <div class="controls">
                <div class="form-group">
                    <label>Mode</label>
                    <select id="mode">
                        <option value="live">Live Trading</option>
                        <option value="backtest">Backtest</option>
                    </select>
                </div>

                <div class="form-group" id="date-group" style="display: none;">
                    <label>Backtest Date</label>
                    <input type="date" id="backtest-date">
                </div>

                <div class="form-group">
                    <label>Execute Orders</label>
                    <select id="live-mode">
                        <option value="false">Paper Trading (Safe)</option>
                        <option value="true">Live Orders ⚠️</option>
                    </select>
                </div>
            </div>

            <div style="margin-top: 20px; display: flex; gap: 15px;">
                <button id="btn-start" class="btn-success" onclick="startTrading()">▶️ Start Auto Trading</button>
                <button id="btn-stop" class="btn-danger" onclick="stopTrading()" disabled>⏹️ Stop</button>
                <button class="btn-primary" onclick="refreshData()">🔄 Refresh</button>
            </div>

            <div style="margin-top: 20px; padding: 15px; background: #fffbeb; border-radius: 8px; border-left: 4px solid #f59e0b;">
                <div style="font-weight: 600; color: #92400e; margin-bottom: 5px;">🧠 Agent-Based Intelligence</div>
                <div style="font-size: 13px; color: #78350f;">
                    News Agent → Technical Agent → Risk Agent → Executor. Full multi-agent validation and decision-making.
                </div>
            </div>
        </div>

        <div class="card">
            <h2 style="margin-bottom: 15px; color: #333;">Activity Log</h2>
            <div id="activity-feed" class="activity-feed">
                <div class="activity-item">
                    <div class="activity-time">UI loaded</div>
                    <div class="activity-text">✅ Frontend is ready</div>
                </div>
            </div>
        </div>
    </div>

    <script>
        let eventSource = null;

        document.getElementById('mode').addEventListener('change', function() {
            const dateGroup = document.getElementById('date-group');
            dateGroup.style.display = this.value === 'backtest' ? 'block' : 'none';
        });

        document.getElementById('backtest-date').value = new Date().toISOString().split('T')[0];

        function addActivity(text) {
            const feed = document.getElementById('activity-feed');
            const item = document.createElement('div');
            item.className = 'activity-item';
            const time = new Date().toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata' });
            item.innerHTML = '<div class="activity-time">' + time + '</div><div class="activity-text">' + text + '</div>';
            feed.insertBefore(item, feed.firstChild);
            while (feed.children.length > 50) { feed.removeChild(feed.lastChild); }
        }

        async function refreshData() {
            try {
                const resp = await fetch('/status');
                const data = await resp.json();

                const marketStatus = document.getElementById('market-status');
                if (data.market && data.market.open) {
                    marketStatus.textContent = 'OPEN';
                    marketStatus.className = 'status-badge status-open';
                } else {
                    marketStatus.textContent = 'CLOSED';
                    marketStatus.className = 'status-badge status-closed';
                }

                document.getElementById('market-time').textContent = (data.market && data.market.time) ? data.market.time : '--:--:--';

                const funds = data.funds || {};
                const available = Number(funds.available_margin || 0);
                document.getElementById('available-margin').textContent = '₹' + available.toLocaleString('en-IN', { maximumFractionDigits: 2 });

                const tradingStatus = document.getElementById('trading-status');
                if (data.trading_active) {
                    tradingStatus.textContent = 'RUNNING';
                    tradingStatus.className = 'status-badge status-running';
                } else {
                    tradingStatus.textContent = 'IDLE';
                    tradingStatus.className = 'status-badge status-idle';
                }
            } catch (error) {
                console.error('Refresh error:', error);
                addActivity('⚠️ Refresh error: ' + error);
            }
        }

        async function startTrading() {
            const mode = document.getElementById('mode').value;
            const date = document.getElementById('backtest-date').value;
            const live = document.getElementById('live-mode').value === 'true';

            if (live && !confirm('⚠️ WARNING: Live trading will use REAL MONEY! Continue?')) return;

            document.getElementById('btn-start').disabled = true;
            document.getElementById('btn-stop').disabled = false;

            addActivity('🚀 Starting... Mode: ' + mode + ', Live: ' + (live ? 'YES ⚠️' : 'Paper'));

            try {
                const resp = await fetch('/start', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mode, date, live })
                });
                const result = await resp.json();
                if (result.status === 'ok') {
                    addActivity('✅ Trading started successfully');
                } else {
                    addActivity('❌ Error: ' + (result.message || 'Unknown'));
                    document.getElementById('btn-start').disabled = false;
                    document.getElementById('btn-stop').disabled = true;
                }
            } catch (error) {
                addActivity('❌ Failed: ' + error);
                document.getElementById('btn-start').disabled = false;
                document.getElementById('btn-stop').disabled = true;
            }
        }

        async function stopTrading() {
            try {
                await fetch('/stop', { method: 'POST' });
                addActivity('⏹️ Trading stopped');
                document.getElementById('btn-start').disabled = false;
                document.getElementById('btn-stop').disabled = true;
            } catch (error) {
                addActivity('❌ Failed to stop: ' + error);
            }
        }

        function connectSSE() {
            if (eventSource) eventSource.close();
            eventSource = new EventSource('/stream');
            eventSource.onmessage = function(e) {
                try {
                    const payload = JSON.parse(e.data);
                    if (payload.event !== 'heartbeat' && payload.message) addActivity(payload.message);
                } catch (err) {}
            };
            eventSource.onerror = function() { setTimeout(connectSSE, 5000); };
        }

        setInterval(refreshData, 3000);
        setInterval(() => {
            const time = new Date().toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata' });
            document.getElementById('market-time').textContent = time;
        }, 1000);

        connectSSE();
        refreshData();
    </script>
</body>
</html>
{% endraw %}
"""

def emit_status(message: str):
    status_queue.put({
        "event": "update",
        "message": message,
        "timestamp": datetime.now(IST).isoformat()
    })

def _crew_status_to_text(payload: dict) -> str:
    event = payload.get("event", "")
    data = payload.get("data", {}) or {}
    sym = data.get("symbol")
    if event == "market_wait_start": return "⏳ Waiting for market to open…"
    if event == "market_status":     return f"📟 Market status: {'OPEN' if data.get('open') else 'CLOSED'} (phase: {data.get('phase')})"
    if event == "review_holdings_start": return "🔍 Reviewing existing holdings…"
    if event == "reviewing_holding" and sym: return f"🧾 Reviewing holding: {sym}"
    if event == "holding_action" and sym:    return f"📌 {sym}: {data.get('action','').upper()} — {data.get('reason','')[:120]}"
    if event == "decide_start" and sym:      return f"🧮 Deciding trade for {sym}…"
    if event == "decision_analyzing" and sym:return f"🔎 Analyzing news & technicals for {sym}…"
    if event == "decide_complete" and sym:
        dirn = data.get("direction"); conf = data.get("confidence")
        return f"✅ Decision {sym}: {dirn} (conf={conf})" if conf is not None else f"✅ Decision {sym}: {dirn}"
    if event == "sizing_start" and sym:      return f"📐 Sizing position for {sym}…"
    if event == "sizing_complete" and sym:   return f"📝 Plan ready for {sym}"
    if event == "execution_complete" and sym:return f"🚦 Executed flow for {sym}"
    if event == "cycle_complete":            return "✅ Decision cycle complete."
    return f"ℹ️ {event}"

# ---------- NEW: pure news-based discovery using UpstoxTechnicalClient.resolve ----------
def discover_and_validate_symbols(mode, date):
    """
    Discover tradable NSE/BSE symbols purely from latest news.
    No static TRADING_SYMBOLS. We:
      - Fetch recent news
      - Extract hints (symbol/company/headline/title)
      - Pass each hint to UpstoxTechnicalClient.resolve()
      - Keep unique NSE_EQ| / BSE_EQ| instruments
    """
    emit_status("🔍 Discovering stocks from recent news (no static watchlist)…")

    if not (NewsClient and UpstoxTechnicalClient):
        emit_status("❌ News/Technical client not available")
        return []

    news_client = NewsClient()
    tech_client = UpstoxTechnicalClient()

    today_param = date if (mode == "backtest" and date) else None
    news_data = news_client.get_recent_news_and_calls(
        today=today_param,
        lookback_days=2,
        max_items=50,
        mode=mode,
        compact=True
    )

    if not isinstance(news_data, list):
        news_data = news_data or []

    emit_status(f"📰 Fetched {len(news_data)} news items")

    validated = []
    seen_ik = set()

    def _hint_strings(item):
        """Collect possible company/symbol hints from one news item."""
        hints = []

        # Direct symbol-like fields
        for key in ("symbol", "ticker"):
            v = item.get(key)
            if isinstance(v, str):
                hints.append(v)

        # Arrays / company fields
        for key in ("symbols", "companies", "company"):
            v = item.get(key)
            if isinstance(v, str):
                hints.append(v)
            elif isinstance(v, (list, tuple)):
                hints.extend([x for x in v if isinstance(x, str)])

        # Headlines / titles – this is where your resolve() magic kicks in
        title = item.get("headline") or item.get("title")
        if isinstance(title, str):
            hints.append(title)

        # Deduplicate (case-insensitive) while preserving order
        out = []
        seen_local = set()
        for h in hints:
            h = h.strip()
            if not h:
                continue
            key = h.lower()
            if key in seen_local:
                continue
            seen_local.add(key)
            out.append(h)
        return out

    for item in news_data:
        hints = _hint_strings(item)
        for hint in hints:
            try:
                row = tech_client.resolve(hint)
            except Exception:
                continue
            if not row:
                continue

            ik = row.get("instrument_key")
            if not ik or not ik.startswith(("NSE_EQ|", "BSE_EQ|")):
                continue
            if ik in seen_ik:
                continue

            seen_ik.add(ik)
            sym = row.get("symbol") or hint
            name = row.get("name") or hint

            validated.append({
                "symbol": sym,
                "name": name,
                "instrument_key": ik,
                "source": hint
            })
            emit_status(f"✅ From news: {hint} → {sym} ({name})")

            if len(validated) >= MAX_DISCOVERED_SYMBOLS:
                break
        if len(validated) >= MAX_DISCOVERED_SYMBOLS:
            break

    if not validated:
        emit_status("⚠️ No tradable NSE/BSE equities could be resolved from today's news.")
    else:
        emit_status(f"🎉 Discovered {len(validated)} symbols from news for this cycle")

    return validated

def trading_loop(mode, date, live):
    global trading_active
    try:
        trading_active = True
        emit_status(f"🚀 Trading system started (mode={mode}, date={date}, live={'ON' if live else 'OFF'})")

        operator = UpstoxOperator() if UpstoxOperator else None
        if mode == "live" and operator:
            try:
                status = operator.market_session_status()
                if not status.get("open"):
                    emit_status("⏳ Market closed, waiting for open...")
                    while not operator.market_session_status().get("open"):
                        if not trading_active: return
                        time.sleep(30)
                    emit_status("✅ Market opened!")
            except Exception as e:
                emit_status(f"⚠️ Market status error: {e}")

        validated_symbols = discover_and_validate_symbols(mode, date)
        if not validated_symbols:
            emit_status("❌ No valid symbols to trade")
            return

        emit_status("🧠 Initializing AI agents...")
        crew = TradingCrew(mode=mode, today=date, live=live)

        def _cb(payload: dict):
            try:
                text = _crew_status_to_text(payload)
                if text: emit_status(text)
            except Exception:
                pass

        crew.add_status_callback(_cb)

        emit_status(f"📊 Running decision cycle for {len(validated_symbols)} symbols...")
        symbols_to_trade = [s["symbol"] for s in validated_symbols]

        try:
            results = crew.run_decision_cycle(symbols_to_trade)
            decisions = results.get("decisions", [])
            executions = results.get("executions", [])
            emit_status(f"✅ Cycle complete: {len(decisions)} decisions, {len(executions)} executions")
            buy_count  = sum(1 for d in decisions if d.get("direction") == "BUY")
            sell_count = sum(1 for d in decisions if d.get("direction") == "SELL")
            skip_count = sum(1 for d in decisions if d.get("direction") == "SKIP")
            emit_status(f"📈 Summary: {buy_count} BUY, {sell_count} SELL, {skip_count} SKIP")
        except Exception as e:
            emit_status(f"❌ Trading cycle error: {str(e)}")

        emit_status("✅ Trading complete!")

    except Exception as e:
        emit_status(f"❌ System error: {str(e)}")
    finally:
        trading_active = False
        emit_status("⏹️ System stopped")

def status_stream():
    def event_stream():
        while True:
            try:
                try:
                    payload = status_queue.get(timeout=1)
                    yield "data: " + json.dumps(payload) + "\n\n"
                except queue.Empty:
                    yield "data: " + json.dumps({"event": "heartbeat"}) + "\n\n"
            except GeneratorExit:
                break
    return Response(event_stream(), mimetype="text/event-stream")

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/health")
def health():
    return jsonify({"ok": True, "time": datetime.now(IST).isoformat()})

@app.route("/status")
def get_status():
    market = {"open": False, "status": "UNKNOWN", "time": datetime.now(IST).strftime("%H:%M:%S")}
    funds_equity = {"available_margin": 0.0}
    # guard broker calls
    if UpstoxOperator:
        try:
            op = UpstoxOperator()
            market = op.market_session_status()
            funds_data = op.get_funds()
            funds_equity = (funds_data or {}).get("equity", {}) or funds_equity
        except Exception as e:
            app.logger.warning("status error: %s", e)
    return jsonify({
        "market": market,
        "funds": funds_equity,
        "trading_active": trading_active,
        "timestamp": datetime.now(IST).isoformat()
    })

@app.route("/start", methods=["POST"])
def start_trading():
    global trading_active
    if trading_active:
        return jsonify({"status": "error", "message": "Already running"})
    data = request.json or {}
    mode = data.get("mode", "live")
    date = data.get("date")
    live = bool(data.get("live", False))
    threading.Thread(target=trading_loop, args=(mode, date, live), daemon=True).start()
    return jsonify({"status": "ok"})

@app.route("/stop", methods=["POST"])
def stop_trading():
    global trading_active
    trading_active = False
    return jsonify({"status": "ok"})

@app.route("/stream")
def stream():
    return status_stream()

if __name__ == "__main__":
    print("\n" + "="*70)
    print("🤖 AI TRADING SYSTEM - AGENT-BASED VERSION")
    print("="*70)
    print("🔗 Open: http://localhost:5000")
    print("⏰ Time:", datetime.now(IST).strftime("%H:%M:%S IST"))
    print("="*70 + "\n")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True, use_reloader=False)
