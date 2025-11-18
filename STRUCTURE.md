# 📂 PROJECT STRUCTURE

## Visual File Organization

```
tradegoo/
│
├── 🚀 MAIN ENTRY POINTS
│   ├── main.py                     # Web UI (Flask dashboard)
│   └── trading_crew.py             # CLI & main trading orchestrator
│
├── 🤖 AI AGENTS & INTELLIGENCE
│   ├── agents.py                   # 8 specialized AI agents
│   ├── crew_tools.py               # Tools for agents to use
│   └── watchlist_manager.py        # Watchlist & pattern learning
│
├── 📊 MARKET DATA & EXECUTION
│   ├── upstox_operator.py          # Broker API (orders, positions, funds)
│   ├── upstox_technical.py         # Technical indicators (RSI, EMA, MACD, etc.)
│   ├── news_client.py              # News fetching (Moneycontrol, Brave)
│   └── brokerage.py                # Fee/charge calculator
│
├── 📖 DOCUMENTATION
│   ├── README.md                   # Project overview (start here!)
│   ├── FIXES_APPLIED.md            # Complete technical documentation
│   └── STRUCTURE.md                # This file
│
├── ⚙️ CONFIGURATION
│   ├── .env                        # Environment variables (create this)
│   └── .gitignore                  # Git ignore rules
│
└── 💾 DATA (auto-created)
    └── data/
        ├── decisions-YYYY-MM-DD.json   # Daily decisions
        ├── ledger.jsonl                # Execution history
        ├── memory.json                 # Learned parameters
        ├── watchlist.json              # Active watchlist
        ├── holdings.json               # Swing positions
        └── crew_tools.log              # Detailed logs
```

---

## 📋 FILE DESCRIPTIONS

### 🚀 Main Entry Points

#### **main.py**
- **What**: Web UI (Flask app)
- **When to use**: Start web dashboard at http://localhost:5000
- **Key features**:
  - Real-time trading status
  - Start/stop trading
  - View funds, positions
  - Server-Sent Events for live updates
- **Run**: `python main.py`

#### **trading_crew.py**
- **What**: Main trading orchestrator
- **When to use**: CLI trading or scheduled runs
- **Key features**:
  - Decision cycle (discover → analyze → execute)
  - Capital tracking
  - Position management
  - Intraday square-off
- **Run**: `python trading_crew.py --mode live --live`

---

### 🤖 AI Agents & Intelligence

#### **agents.py**
- **What**: 8 specialized AI agents
- **Agents**:
  1. **News Agent**: Sentiment scoring from news (-1 to +1)
  2. **Technical Agent**: Multi-timeframe analysis (30m, daily)
  3. **Lead Agent**: Combine news + technicals → BUY/SELL/SKIP
  4. **Entry Validator**: Score entry quality (0-100)
  5. **Risk Agent**: Position sizing, SL, target calculation
  6. **Executor Agent**: Place orders with mandatory SL
  7. **Monitor Agent**: Watch positions, trail stops
  8. **Learner Agent**: Optimize parameters from history
- **Tech**: OpenAI GPT models via CrewAI

#### **crew_tools.py**
- **What**: 16+ tools that agents use
- **Categories**:
  - News tools: `get_recent_news_tool`, `search_news_tool`
  - Technical tools: `get_technical_snapshot_tool`
  - Broker tools: `get_funds_tool`, `place_order_tool`, `square_off_tool`
  - Utility tools: `calculate_trade_metrics_tool`, `round_to_tick_tool`
- **Key feature**: All tools return JSON, fully logged

#### **watchlist_manager.py**
- **What**: Professional watchlist system
- **Features**:
  - **Intraday watchlist**: Monitor during session
  - **Tomorrow's queue**: Carry forward incomplete setups
  - **Pattern learning**: Best performers by time of day
  - **Success tracking**: Win rate, average move per symbol
- **Use cases**:
  - Stock has BUY signal but entry quality = 55 (not 70+) → Watchlist
  - Check watchlist every 15-30 min for better entries
  - Learn which stocks work best at different times

---

### 📊 Market Data & Execution

#### **upstox_operator.py**
- **What**: Complete Upstox broker API wrapper
- **Key methods**:
  - `get_funds()` - Available margin
  - `get_positions()` - Current positions
  - `place_order()` - Place bracket order with SL/target
  - `square_off()` - Close position
  - `market_session_status()` - Check if market open
- **Safety**: Mandatory stop-loss, strict live mode, dry-run default
- **CLI**: `python upstox_operator.py --funds` (check balance)

#### **upstox_technical.py**
- **What**: Technical analysis client
- **Indicators**:
  - RSI (14)
  - EMA (20, 50)
  - MACD histogram
  - ATR (volatility)
  - VWAP
- **Timeframes**: 5m, 15m, 30m, 1h, daily
- **Output**: Clean JSON snapshots

#### **news_client.py**
- **What**: News aggregation
- **Sources**:
  - Moneycontrol (scraping)
  - Brave News API (optional)
- **Output**: Compact format (headline, date, source, URL, summary)
- **CLI**: `python news_client.py --recent --days 2`

#### **brokerage.py**
- **What**: Trading fee calculator for Indian equities
- **Includes**:
  - Brokerage (₹20/order default)
  - Exchange charges
  - SEBI fees
  - GST (18%)
  - STT (delivery: 10 bps, intraday: 2.5 bps)
  - Stamp duty
- **Use**: Estimate costs before trading

---

## 🔄 DATA FLOW

```
1. USER STARTS TRADING
   ↓
2. main.py (Web UI) OR trading_crew.py (CLI)
   ↓
3. trading_crew.py.run_decision_cycle()
   ├─ Discover stocks (news_client.py)
   ├─ For each stock:
   │   ├─ News Agent (crew_tools.py → news_client.py)
   │   ├─ Technical Agent (crew_tools.py → upstox_technical.py)
   │   ├─ Lead Agent (decides BUY/SELL)
   │   │
   │   ├─ Entry Validator Agent (scores 0-100)
   │   │   ├─ If ≥70: Continue to execution
   │   │   ├─ If 50-69: Add to watchlist_manager.py
   │   │   └─ If <50: Skip
   │   │
   │   ├─ Risk Agent (size position)
   │   │   └─ crew_tools.py → upstox_operator.py (get funds)
   │   │
   │   └─ Executor Agent (place order)
   │       └─ crew_tools.py → upstox_operator.py (place_order)
   │
   ├─ Monitor positions
   └─ Square off intraday at 3:10 PM
   ↓
4. Save to data/ directory
```

---

## 🎯 WHERE TO START

### For Beginners
1. Read **README.md** (overview)
2. Look at **main.py** (web UI is simpler)
3. Play with web UI at http://localhost:5000

### For Developers
1. Read **FIXES_APPLIED.md** (technical details)
2. Study **trading_crew.py** (main logic)
3. Read **agents.py** (understand AI agents)
4. Look at **upstox_operator.py** (broker API)

### For Traders
1. Read **README.md** (setup & config)
2. Configure `.env` properly
3. Run in paper mode first
4. Monitor `data/ledger.jsonl`

---

## 🔧 DEVELOPMENT

### Adding a New Agent
1. Edit `agents.py` → Add `create_your_agent()`
2. Edit `agents.py` → Add to `create_all_agents()`
3. Edit `trading_crew.py` → Use the agent

### Adding a New Tool
1. Edit `crew_tools.py` → Add `@tool` decorator
2. Add to tool list at bottom
3. Agent prompts can now reference it

### Modifying Trading Logic
1. Main logic: `trading_crew.py`
2. Agent prompts: `agents.py`
3. Tool implementation: `crew_tools.py`

---

## 📦 DEPENDENCIES

```
Core:
- crewai (multi-agent orchestration)
- openai (GPT models)
- requests (API calls)
- flask (web UI)

Data:
- pandas (data manipulation)
- numpy (calculations)

Utilities:
- python-dotenv (environment vars)
- zoneinfo (timezone handling)
```

---

## 🗂️ FILE SIZE REFERENCE

```
upstox_operator.py    ~36KB  (comprehensive broker API)
trading_crew.py       ~43KB  (main orchestrator)
crew_tools.py         ~43KB  (16+ tools)
upstox_technical.py   ~24KB  (technical indicators)
main.py               ~24KB  (web UI)
agents.py             ~21KB  (8 AI agents)
watchlist_manager.py  ~19KB  (watchlist system)
FIXES_APPLIED.md      ~19KB  (documentation)
news_client.py        ~17KB  (news fetching)
brokerage.py          ~4KB   (fee calculator)
```

Total: ~250KB of Python code

---

## 📊 COMPLEXITY LEVELS

| File | Complexity | Should You Edit? |
|------|-----------|------------------|
| main.py | ⭐⭐ Medium | Rarely (UI only) |
| trading_crew.py | ⭐⭐⭐⭐ High | Sometimes (workflow) |
| agents.py | ⭐⭐⭐ Medium-High | Often (prompts) |
| crew_tools.py | ⭐⭐⭐ Medium-High | Sometimes (tools) |
| upstox_operator.py | ⭐⭐⭐ Medium-High | Rarely (stable) |
| upstox_technical.py | ⭐⭐ Medium | Rarely (stable) |
| watchlist_manager.py | ⭐⭐ Medium | Sometimes (features) |
| news_client.py | ⭐⭐ Medium | Rarely (stable) |
| brokerage.py | ⭐ Low | Rarely (just math) |

---

**This structure makes it easy to understand what each file does and where to look for specific functionality!**
