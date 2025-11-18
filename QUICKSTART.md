# ⚡ QUICK START GUIDE

Get trading in **5 minutes** or less!

---

## 🎯 What You Need

1. **Upstox Account** (with API access)
2. **OpenAI API Key** (for AI agents)
3. **Python 3.10+** installed
4. **₹5,000-₹10,000** capital (recommended for testing)

---

## 📋 STEP 1: Environment Setup (2 minutes)

Create a file named `.env` in the project folder:

```bash
# Copy this exactly and replace with your keys:

UPSTOX_ACCESS_TOKEN=your_upstox_token_here
OPENAI_API_KEY=your_openai_key_here

# Trading limits (recommended)
MAX_POSITIONS=3
MAX_CAPITAL_UTILIZATION=0.85
MODE=live
```

### Where to get tokens:

**Upstox Token**:
1. Go to https://api.upstox.com/
2. Login → Create App
3. Get Access Token

**OpenAI Key**:
1. Go to https://platform.openai.com/api-keys
2. Create new secret key

---

## 📦 STEP 2: Install Dependencies (1 minute)

```bash
pip install crewai openai requests flask pandas numpy python-dotenv
```

Or if you have `requirements.txt`:
```bash
pip install -r requirements.txt
```

---

## 🚀 STEP 3: Run the System (1 minute)

### Option A: Web UI (Easiest!)

```bash
python main.py
```

Then open in browser: **http://localhost:5000**

You'll see:
- 💰 Current funds
- 📊 Positions
- ▶️ Start Trading button
- 📋 Live activity feed

Click **"Start Trading"** and watch it work!

---

### Option B: Command Line

```bash
# Paper trading (safe, won't place real orders)
python trading_crew.py --mode live

# Real trading (live orders)
python trading_crew.py --mode live --live

# Backtest specific stocks
python trading_crew.py --mode backtest --symbols IRFC,AAVAS,ITC
```

---

## ✅ STEP 4: Verify It's Working

### Check Console Output

You should see:
```
💰 Starting capital: ₹8,000.00
🎯 Processing IRFC (Position 1/3)
📊 Entry Quality: 75/100 → ENTER_NOW
✅ Position opened: 1/3
```

### Check Web UI

- Live updates appear in real-time
- See decisions being made
- Watch positions open/close

### Check Data Files

```bash
# Today's decisions
cat data/decisions-$(date +%Y-%m-%d).json

# Execution history
cat data/ledger.jsonl

# Tool logs
cat data/crew_tools.log
```

---

## 🎓 UNDERSTANDING THE OUTPUT

### Decision Output

```json
{
  "symbol": "IRFC",
  "direction": "BUY",
  "confidence": 0.68,
  "style": "intraday",
  "entry_quality_score": 75,
  "status": "executed"
}
```

**What this means**:
- Found IRFC stock
- Decided to BUY (68% confidence)
- Intraday trade (will square off at 3:10 PM)
- Entry quality: 75/100 (good!)
- Successfully executed

---

## 🛡️ SAFETY CHECKLIST

Before going live, verify:

- [ ] `.env` file created with correct tokens
- [ ] MAX_POSITIONS=3 (conservative start)
- [ ] Capital amount is correct (₹5K-₹10K for testing)
- [ ] Tested in paper mode first (remove `--live` flag)
- [ ] Market is open (Mon-Fri 9:15 AM - 3:30 PM IST)

---

## 📊 FIRST RUN CHECKLIST

### ✅ Good Signs

- Console shows capital correctly (₹X,XXX.XX)
- Discovers 10-20 stocks from news
- Makes 2-5 decisions (BUY/SELL/SKIP)
- Entry quality scores appear (0-100)
- Shows "Position opened: 1/3"

### ⚠️ Warning Signs

- Capital shows ₹0 → Check UPSTOX_ACCESS_TOKEN
- "invalid_risk_plan" errors → Update to latest code
- No stocks discovered → Check internet connection
- OpenAI errors → Check OPENAI_API_KEY

---

## 🎯 COMMON FIRST-RUN SCENARIOS

### Scenario 1: All Stocks Skipped
```
📊 Entry Quality: 45/100 → SKIP
```
**Normal!** System is being selective. Only 20-30% of signals result in entries.

### Scenario 2: Added to Watchlist
```
📋 Adding IRFC to watchlist (quality: 62)
```
**Good!** Stock has potential but entry timing not optimal. Will check again later.

### Scenario 3: Capital Exhausted
```
⚠️ Capital utilization 87.3% >= 85.0%, stopping new trades
```
**Safe!** System protecting you from overtrading. Adjust MAX_CAPITAL_UTILIZATION if needed.

### Scenario 4: Max Positions Reached
```
⚠️ Max positions (3) reached, skipping remaining symbols
```
**Perfect!** Risk management working. Can increase MAX_POSITIONS to 5 after testing.

---

## 🚨 TROUBLESHOOTING

### Problem: "UPSTOX_ACCESS_TOKEN missing"
**Solution**: Create `.env` file with your token

### Problem: Capital shows ₹0
**Solution**:
1. Check token is valid
2. Test with: `python upstox_operator.py --funds`
3. Verify account has funds

### Problem: No stocks discovered
**Solution**: Check internet, news sources may be down, try again

### Problem: OpenAI rate limit error
**Solution**:
1. Wait a few minutes
2. Or upgrade OpenAI plan
3. Or reduce MAX_DISCOVERED_SYMBOLS to 10

### Problem: Market closed error
**Solution**: Normal! Run during market hours (9:15 AM - 3:30 PM IST Mon-Fri)

---

## 📈 NEXT STEPS

After your first successful run:

1. **Review Results**
   ```bash
   cat data/decisions-$(date +%Y-%m-%d).json
   ```

2. **Check Watchlist**
   ```python
   from watchlist_manager import get_watchlist_manager
   wm = get_watchlist_manager()
   print(wm.get_status())
   ```

3. **Understand the Agents**
   - Read `STRUCTURE.md` for file details
   - Read `FIXES_APPLIED.md` for technical details

4. **Gradually Increase**
   - Start: MAX_POSITIONS=2, ₹5K capital
   - After 5 good trades: MAX_POSITIONS=3, ₹10K
   - After 10 good trades: MAX_POSITIONS=5, ₹20K+

---

## 💡 PRO TIPS

### Tip 1: Monitor During Market Hours
Best times to run:
- **9:20-9:45 AM**: Opening range (high activity)
- **10:30-11:30 AM**: Mid-morning (stable)
- **2:00-3:00 PM**: Afternoon (momentum)

### Tip 2: Let It Run
Don't interfere during execution. System handles:
- Entry validation
- Position sizing
- Stop-loss placement
- Square-off at 3:10 PM

### Tip 3: Review End of Day
At 3:30 PM, review:
1. `data/decisions-[today].json`
2. `data/ledger.jsonl`
3. What worked, what didn't

### Tip 4: Trust the System
- Entry quality < 70? → Correctly skipped
- Added to watchlist? → Smart, waiting for better price
- Max positions reached? → Good risk management

---

## 🎉 YOU'RE READY!

```bash
# Start trading now:
python main.py
```

Open http://localhost:5000 and click **"Start Trading"**!

---

## 📚 MORE HELP

- **Project overview**: `README.md`
- **File structure**: `STRUCTURE.md`
- **Technical details**: `FIXES_APPLIED.md`
- **Logs**: `data/crew_tools.log`
- **Errors**: `data/incidents.jsonl`

---

**Happy Trading! 🚀**

*Remember: Start in paper mode, test thoroughly, then go live with small capital!*
