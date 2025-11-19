# Live Trading Setup Guide

## ⚠️ IMPORTANT: Live Trading vs Paper Trading

By default, the system runs in **PAPER TRADING** mode, which means:
- ✅ All trading logic is executed
- ✅ AI agents analyze stocks and make decisions
- ✅ Trading signals are generated
- ❌ **NO real orders are placed on Upstox**
- ❌ **NO real money is used**

To enable **LIVE TRADING** (real orders with real money), you need to:

---

## 🔧 Step 1: Set Required Environment Variables

### Required Variables

Create a `.env` file in the project root with the following:

```bash
# REQUIRED: OpenAI API key for AI agents
OPENAI_API_KEY=sk-your-actual-openai-api-key-here

# REQUIRED: Upstox access token for broker API
UPSTOX_ACCESS_TOKEN=your-upstox-access-token-here

# REQUIRED: Set MODE to 'live' for live trading
MODE=live

# RECOMMENDED: Set a secure Flask secret key
FLASK_SECRET_KEY=your-secure-random-string-here
```

### How to Get Your Upstox Access Token

1. Log in to [Upstox Developer Portal](https://api.upstox.com/)
2. Create an API app
3. Get your API Key and Secret
4. Use the OAuth flow to get an access token
5. Set the token in your `.env` file

**Note:** Upstox access tokens expire daily, so you'll need to refresh them regularly.

---

## 🖥️ Step 2: Enable Live Mode in the UI

Once your environment variables are set:

1. **Start the application:**
   ```bash
   python main.py
   ```

2. **Open the UI:** http://localhost:5000

3. **Configure trading settings:**
   - Set **Mode** to "Live Trading"
   - Set **Execute Orders** to "Live Orders ⚠️" (this is the critical setting!)
   - Configure Max Companies, Learning Mode, etc.

4. **Click "Start Auto Trading"**
   - You'll see a configuration check
   - If everything is configured, you'll get a final confirmation
   - Click OK to start live trading with real money

---

## 📊 How to Verify Live Trading is Enabled

### In the UI:
- Activity log will show: `🔴 LIVE TRADING MODE - Real orders will be placed on Upstox`
- When orders are placed, you'll see: `🔴 LIVE ORDER: {symbol} order WILL BE EXECUTED on Upstox`

### In the Logs:
Check the log file in `./logs/trading_YYYYMMDD.log`:
```
🔴 LIVE TRADING MODE ENABLED - Real money will be used!
🔴 LIVE TRADING: Placing real order for {symbol} on Upstox
```

### Paper Trading Indicators:
If you see these, you're in paper trading mode (no real orders):
```
📝 PAPER TRADING MODE - No real orders will be placed
⚠️  PAPER TRADING: {symbol} order will NOT be executed on Upstox
```

---

## 🔒 Security Configuration

The system has several safety mechanisms:

### 1. **Strict Live Mode** (default: enabled)
```bash
STRICT_LIVE_MODE=1  # Default: enabled
```
When enabled, the system requires:
- `MODE=live` to be set
- `UPSTOX_ACCESS_TOKEN` to be present
- Otherwise, all orders are blocked

### 2. **Mandatory Stop-Loss**
Every live order MUST have a stop-loss:
- Absolute: `stop_loss=445.5`
- Percentage: `stop_loss_pct=0.5` (0.5%)

Orders without stop-loss will be rejected.

### 3. **Double Confirmation**
When you select "Live Orders ⚠️" in the UI, you get:
1. Configuration validation check
2. Final warning popup: "⚠️ WARNING: Live trading will use REAL MONEY!"

---

## 🧪 Testing Live Trading Setup

### Test 1: Check Configuration Status
```bash
curl http://localhost:5000/config-status
```

Expected response:
```json
{
  "live_trading_ready": true,
  "has_upstox_token": true,
  "has_openai_key": true,
  "mode": "live",
  "strict_live_mode": true,
  "warnings": []
}
```

### Test 2: Dry Run First
Before enabling live orders:
1. Keep "Execute Orders" set to "Paper Trading"
2. Start trading
3. Watch the logs to see what orders would be placed
4. Verify the AI logic is working correctly

### Test 3: Single Small Order
For your first live trade:
1. Set **Max Companies to Analyze** to `1`
2. Enable **Live Orders ⚠️**
3. Monitor closely in the Upstox app
4. Verify orders are placed correctly

---

## ❌ Common Issues

### Issue: "Live trading not ready" error

**Symptoms:** Alert in UI says "LIVE TRADING NOT READY"

**Solutions:**
1. Check `.env` file has `UPSTOX_ACCESS_TOKEN` set
2. Check `.env` file has `OPENAI_API_KEY` set
3. If `STRICT_LIVE_MODE=1`, ensure `MODE=live`
4. Restart the application after changing `.env`

### Issue: Orders show as "dry_run: true"

**Symptoms:** In logs, you see `"dry_run": true` or `"live": false`

**Solutions:**
1. In UI, check "Execute Orders" is set to "Live Orders ⚠️", not "Paper Trading"
2. Verify the system detected your setting (check activity log)

### Issue: Upstox API errors

**Symptoms:** HTTP 401, 403, or "invalid token" errors

**Solutions:**
1. Your Upstox access token has expired (tokens expire daily)
2. Generate a new access token
3. Update `.env` file with new token
4. Restart the application

---

## 📝 Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | Yes | - | OpenAI API key for AI agents |
| `UPSTOX_ACCESS_TOKEN` | Yes (for live) | - | Upstox broker access token |
| `MODE` | No | `live` | Trading mode: `live`, `backtest`, or `paper` |
| `STRICT_LIVE_MODE` | No | `1` | Enforce safety checks for live trading |
| `FLASK_SECRET_KEY` | Recommended | Auto-generated | Flask session secret key |
| `MAX_DISCOVERED_SYMBOLS` | No | `20` | Max companies to analyze per cycle |

---

## 🚨 Safety Reminders

1. **Always test in paper trading mode first**
2. **Start with small position sizes**
3. **Monitor the first few trades closely**
4. **Check Upstox app to verify orders**
5. **Ensure sufficient funds in your account**
6. **Set appropriate stop-losses**
7. **Be aware of market hours (9:15 AM - 3:30 PM IST)**
8. **Keep your access tokens secure**

---

## 🆘 Emergency Stop

If something goes wrong:

1. **Click the "Stop" button in the UI** - This stops the trading loop
2. **Check your Upstox app** - Manually square off any unwanted positions
3. **Check the logs** - Review what orders were placed in `./logs/trading_YYYYMMDD.log`

---

## 📚 Additional Resources

- [Upstox API Documentation](https://upstox.com/developer/api-documentation/)
- [Project README](./README.md)
- [Issue Tracker](https://github.com/venkat7568/tradegoo/issues)

---

**Last Updated:** 2025-11-20
**Version:** 2.1
