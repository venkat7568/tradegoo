# CRITICAL ISSUES WITH CURRENT TRADING SYSTEM

## Issue 1: Orders Not Being Placed Despite "Live Mode" Message

### Root Cause:
The tools in `crew_tools.py` use a **GLOBAL operator** instance (`OP = UpstoxOperator()`) created at import time. This operator does NOT receive the `live=True` parameter from the web UI - it's a separate instance from the one used in TradingCrew.

### Current Flow:
1. User clicks "Start Trading" with "Live Orders ⚠️" selected
2. TradingCrew.__init__() sets `self.live = True`
3. Executor agent is told to pass `"live": true` to place_order_tool
4. **BUT** place_order_tool calls `OP.place_order(live=true)`
5. OP is the global instance that doesn't know about the session's live setting

### Fix Needed:
The TradingCrew needs to pass its own operator instance to the tools, OR the tools need to get the live parameter from somewhere consistent.

---

## Issue 2: Capital Confusion - Margin vs Real Money

### Current Situation:
- Real cash balance: **₹7,686**
- Order placed: 285 shares @ ₹351 = **₹100,040 position value**
- System shows: "Available: ₹7,686.13, Used: ₹0.00"

### The Problem:
The `get_funds()` API returns **"available_margin"** which includes leverage, NOT your actual cash. For intraday trading, brokers provide 5-20x leverage, so ₹7,686 margin can open a ₹100,000+ position.

**This is EXTREMELY RISKY because:**
- You're trading with borrowed money (leverage)
- If the trade goes against you, losses are amplified
- A 3% loss on ₹100,040 = ₹3,001 loss (39% of your capital!)
- The stop-loss at ₹348 means max loss = ₹858 (11% of capital)

### Fix Needed:
1. Show separate displays for "Cash Balance" vs "Margin Available"
2. Limit position sizes to a safer percentage of CASH, not margin
3. Add warnings when using more than 2x leverage

---

## Issue 3: Capital Tracking Not Updating After Trades

After the SKYGOLD order was placed, the system still showed:
```
💰 Capital: ₹7,686.13 available (used: ₹0.00, 0.0%)
```

This means capital tracking is not updating in real-time, which could lead to over-trading.

### Fix Needed:
Refresh capital status after each trade execution.

---

## IMMEDIATE ACTION REQUIRED:

1. **Check Upstox account** - Is there actually a SKYGOLD order?
2. **If NO** - System is in paper trading mode (my diagnosis)
3. **If YES** - You're using heavy leverage which is VERY RISKY

Would you like me to:
A) Fix the operator instance issue so live trading actually works?
B) Add proper capital/leverage warnings and limits?
C) Both?
