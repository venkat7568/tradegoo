#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trading_crew.py — Main orchestration for the AI trading system (brace-safe)
===========================================================================

- Uses string.Template with $placeholders so JSON braces never break formatting.
- Matches agents.py contracts (JSON-only agents, Executor uses place_order_tool).
- Streams status via callbacks, persists decisions and ledger.
"""

from __future__ import annotations

import os
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, List, Any, Optional, Sequence
from pathlib import Path
from string import Template

from crewai import Crew, Task, Process

# Agents & tools
from agents import create_all_agents
from symbol_validator import normalize_symbol, validate_symbol_list
from crew_tools import (
    get_recent_news_tool,
    search_news_tool,
    get_technical_snapshot_tool,
    get_market_status_tool,
    get_funds_tool,
    get_positions_tool,
    get_holdings_tool,
    get_portfolio_summary_tool,
    calculate_margin_tool,
    calculate_max_quantity_tool,
    place_order_tool,
    place_intraday_bracket_tool,  # CRITICAL FIX: Was missing - executor couldn't place intraday orders!
    square_off_tool,
    calculate_trade_metrics_tool,
    get_current_time_tool,
    round_to_tick_tool,
    calculate_atr_stop_tool,
)

# Direct clients (optional/fallbacks)
from upstox_technical import UpstoxTechnicalClient
from news_client import NewsClient

# ============================================================================
# JSON Parsing Utilities - Safer JSON extraction from agent responses
# ============================================================================
import logging
logger = logging.getLogger(__name__)

def safe_parse_json(text: str, fallback: Optional[dict] = None, log_failures: bool = True) -> Optional[dict]:
    """
    Safely parse JSON from text that may contain surrounding content.

    Args:
        text: Input text potentially containing JSON
        fallback: Default value if parsing fails
        log_failures: Whether to log parsing failures

    Returns:
        Parsed dict or fallback value
    """
    if not text:
        return fallback

    text = str(text).strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting JSON from text
    if "{" in text and "}" in text:
        json_start = text.find("{")
        json_end = text.rfind("}") + 1

        if json_end > json_start > 0:
            try:
                json_str = text[json_start:json_end]
                result = json.loads(json_str)
                if log_failures:
                    logger.debug(f"Extracted JSON from text (length: {len(text)} -> {len(json_str)})")
                return result
            except json.JSONDecodeError as e:
                if log_failures:
                    logger.warning(f"Failed to parse extracted JSON: {e}")
                    logger.debug(f"Original text: {text[:200]}...")

    if log_failures:
        logger.error(f"Failed to parse JSON from text: {text[:200]}...")

    return fallback


def validate_technical_data(data: dict, agent_name: str = "unknown") -> bool:
    """
    Validate that technical data came from actual tool calls, not LLM fabrication.

    CRITICAL SAFETY CHECK: GPT-5-mini and other incompatible models have been found to
    fabricate realistic-looking technical data when tool calls fail. This function
    detects such fabrications to prevent trading on hallucinated analysis.

    Args:
        data: Technical analysis data to validate
        agent_name: Name of agent that produced this data (for logging)

    Returns:
        True if data appears legitimate, False if it looks fabricated
    """
    if not isinstance(data, dict):
        logger.warning(f"[{agent_name}] Technical data is not a dict: {type(data)}")
        return False

    # RED FLAG 1: Technical data without tool response wrapper
    # Real tool calls return {"ok": true/false, "symbol": "X", "snapshot": {...}}
    # Fabricated data often has raw fields like {"ref_price": 156.30, "indicators": {...}}
    if "ref_price" in data and "ok" not in data:
        logger.error(f"⚠️ FABRICATED DATA DETECTED from {agent_name}!")
        logger.error(f"   Data contains 'ref_price' but no 'ok' status - likely hallucinated!")
        logger.error(f"   First 200 chars: {str(data)[:200]}")
        return False

    # RED FLAG 2: Indicators without proper tool response structure
    if "indicators" in data and not any(k in data for k in ["ok", "snapshot", "status"]):
        logger.error(f"⚠️ FABRICATED DATA DETECTED from {agent_name}!")
        logger.error(f"   Data contains 'indicators' but lacks tool response structure!")
        logger.error(f"   First 200 chars: {str(data)[:200]}")
        return False

    # RED FLAG 3: Multi-timeframe data without proper nesting
    # Fabricated data often has: {"tf": {"m30": {"trend": "UP", ...}}}
    # Real data has: {"ok": true, "symbol": "X", "snapshot": {"tf": {...}}}
    if "tf" in data and "ok" not in data and "snapshot" not in data:
        logger.error(f"⚠️ FABRICATED DATA DETECTED from {agent_name}!")
        logger.error(f"   Data contains 'tf' (timeframes) but lacks tool response wrapper!")
        logger.error(f"   First 200 chars: {str(data)[:200]}")
        return False

    # Data appears legitimate (has proper tool response structure or is a simple decision)
    return True


# Trade tracker for P&L calculation
from trade_tracker import TradeTracker

# Market context for sentiment and breadth analysis
from market_context import MarketContext

# Position monitor for tracking SL/target hits
from position_monitor import PositionMonitor

# Money manager for capital allocation
from money_manager import MoneyManager

# Learning engine for continuous improvement
from learning_engine import LearningEngine

# Imperative operator for direct actions (optional)
try:
    import upstox_operator as upop
    _OpClass = None
    for _name in ("UpstoxOperator", "Operator", "BrokerOperator"):
        _OpClass = getattr(upop, _name, _OpClass)
    UpstoxOperator = _OpClass or None
except Exception:
    UpstoxOperator = None

IST = ZoneInfo(os.environ.get("TZ", "Asia/Kolkata"))
DATA_DIR = Path("./data")
DATA_DIR.mkdir(exist_ok=True)


def _tmpl(text: str, **kw) -> str:
    """Brace-safe tiny templater using $placeholders (no str.format)."""
    skw = {
        k: (
            v
            if isinstance(v, str)
            else json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list))
            else str(v)
        )
        for k, v in kw.items()
    }
    return Template(text).substitute(**skw)


class TradingCrew:
    """Main trading crew orchestrator."""

    def __init__(
        self,
        mode: str = "live",
        today: Optional[str] = None,
        live: bool = False,
        wait_for_open: bool = False,
        min_confidence_gate: Optional[float] = None,
        crew_verbose: bool = None,
    ):
        self.mode = mode
        self.today = today or datetime.now(IST).strftime("%Y-%m-%d")
        self.live = bool(live)
        self.wait_for_open = bool(wait_for_open)
        self.min_confidence_gate = min_confidence_gate

        # Crew verbosity (default from env, can be overridden)
        if crew_verbose is None:
            self.crew_verbose = os.environ.get("CREW_VERBOSE", "true").lower() in ("true", "1", "yes")
        else:
            self.crew_verbose = bool(crew_verbose)

        # File paths
        self.holdings_file = DATA_DIR / "holdings.json"
        self.decisions_file = DATA_DIR / f"decisions-{self.today}.json"
        self.ledger_file = DATA_DIR / "ledger.jsonl"
        self.memory_file = DATA_DIR / "memory.json"
        self.incidents_file = DATA_DIR / "incidents.jsonl"

        # Load memory
        self.memory = self._load_memory()
        if self.min_confidence_gate is not None:
            self.memory["confidence_gate"] = float(self.min_confidence_gate)

        # Initialize clients
        print("🔧 Initializing clients...")
        try:
            self.tech = UpstoxTechnicalClient()
            print("✅ Technical client initialized")
        except Exception as e:
            print(f"⚠️ Technical client init failed: {e}")
            self.tech = None

        try:
            self.news = NewsClient()
            print("✅ News client initialized")
        except Exception as e:
            print(f"⚠️ News client init failed: {e}")
            self.news = None

        try:
            self.operator = UpstoxOperator() if UpstoxOperator else None
            if self.operator:
                print("✅ Operator initialized")
                # CRITICAL FIX: Inject this operator instance into crew_tools
                # so all tools use the operator with correct live mode settings
                try:
                    from crew_tools import set_operator_instance
                    set_operator_instance(self.operator)
                    if self.live:
                        print(f"🔴 Live trading enabled - tools will execute REAL orders on Upstox")
                    else:
                        print(f"📝 Paper trading mode - tools will simulate orders only")
                except Exception as inject_err:
                    print(f"⚠️ Failed to inject operator into tools: {inject_err}")
            else:
                print("⚠️ Operator not available")
                # FIXED: Fail fast in live mode if operator not available
                if self.live:
                    raise RuntimeError("Cannot run in LIVE mode without operator - trading would fail!")
        except Exception as e:
            print(f"🚨 Operator init failed: {e}")
            # FIXED: Fail fast in live mode
            if self.live:
                raise RuntimeError(f"Cannot run in LIVE mode without operator! Error: {e}")
            self.operator = None

        # Initialize trade tracker for P&L tracking
        try:
            self.tracker = TradeTracker()
            print("✅ Trade tracker initialized")
        except Exception as e:
            print(f"⚠️ Trade tracker init failed: {e}")
            self.tracker = None

        # Initialize market context analyzer
        try:
            self.market_context = MarketContext(tech_client=self.tech)
            print("✅ Market context analyzer initialized")
        except Exception as e:
            print(f"⚠️ Market context init failed: {e}")
            self.market_context = None

        # Initialize position monitor
        try:
            self.position_monitor = PositionMonitor(
                operator=self.operator,
                tech_client=self.tech,
                trade_tracker=self.tracker
            )
            print("✅ Position monitor initialized")
        except Exception as e:
            print(f"⚠️ Position monitor init failed: {e}")
            self.position_monitor = None

        # Initialize money manager
        try:
            self.money_manager = MoneyManager(operator=self.operator)
            print("✅ Money manager initialized")
        except Exception as e:
            print(f"⚠️ Money manager init failed: {e}")
            self.money_manager = None

        # Initialize learning engine
        try:
            self.learning_engine = LearningEngine(trade_tracker=self.tracker)
            print("✅ Learning engine initialized")
        except Exception as e:
            print(f"⚠️ Learning engine init failed: {e}")
            self.learning_engine = None

        # Initialize agents with tools
        all_tools = [
            get_recent_news_tool,
            search_news_tool,
            get_technical_snapshot_tool,
            get_market_status_tool,
            get_funds_tool,
            get_positions_tool,
            get_holdings_tool,
            get_portfolio_summary_tool,
            calculate_margin_tool,
            calculate_max_quantity_tool,
            place_order_tool,
            place_intraday_bracket_tool,  # CRITICAL FIX: Was missing - executor needs this for intraday trades!
            square_off_tool,
            calculate_trade_metrics_tool,
            get_current_time_tool,
            round_to_tick_tool,
            calculate_atr_stop_tool,
        ]
        self.agents = create_all_agents(all_tools)
        print(f"✅ Initialized {len(self.agents)} agents with {len(all_tools)} tools")

        # Status stream callbacks (UI bridge)
        self.status_callbacks = []

    # -------------------------------
    # Persistence helpers
    # -------------------------------
    def _load_memory(self) -> Dict[str, Any]:
        if self.memory_file.exists():
            with open(self.memory_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "w_news": 0.45,
            "w_tech": 0.55,
            "confidence_gate": 0.50,
            "risk_base_pct": 0.60,
            "blacklist": [],
            "symbol_notes": {},
            "last_update": None,
        }

    def _save_memory(self):
        self.memory["last_update"] = datetime.now(IST).isoformat()
        with open(self.memory_file, "w", encoding="utf-8") as f:
            json.dump(self.memory, f, indent=2, ensure_ascii=False)

    def _load_holdings(self) -> List[Dict[str, Any]]:
        if self.holdings_file.exists():
            with open(self.holdings_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def _save_holdings(self, holdings: List[Dict[str, Any]]):
        with open(self.holdings_file, "w", encoding="utf-8") as f:
            json.dump(holdings, f, indent=2, ensure_ascii=False)

    def _append_ledger(self, entry: Dict[str, Any]):
        with open(self.ledger_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _save_decisions(self, decisions: Dict[str, Any]):
        with open(self.decisions_file, "w", encoding="utf-8") as f:
            json.dump(decisions, f, indent=2, ensure_ascii=False)

    def _log_incident(self, incident: Dict[str, Any]):
        incident["timestamp"] = datetime.now(IST).isoformat()
        with open(self.incidents_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(incident, ensure_ascii=False) + "\n")

    # -------------------------------
    # UI status helpers
    # -------------------------------
    def _emit_status(self, event: str, data: Dict[str, Any]):
        payload = {
            "timestamp": datetime.now(IST).isoformat(),
            "event": event,
            "data": data,
        }
        for callback in self.status_callbacks:
            try:
                callback(payload)
            except Exception as e:
                print(f"Status callback error: {e}")

    def add_status_callback(self, callback):
        self.status_callbacks.append(callback)

    # -------------------------------
    # Market session utility
    # -------------------------------
    def _wait_until_open_if_needed(self):
        if not (self.wait_for_open and self.mode == "live"):
            return
        if not self.operator:
            print("⚠️ Operator not available, skipping market wait")
            return

        self._emit_status("market_wait_start", {})
        print("⏳ Waiting for market to open...")

        # SECURITY: Add timeout to prevent infinite waiting
        max_wait_seconds = 3600  # 1 hour maximum wait
        start_time = time.time()

        try:
            while True:
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed > max_wait_seconds:
                    print(f"⚠️ Market wait timeout after {max_wait_seconds}s - proceeding anyway")
                    self._emit_status("market_wait_timeout", {"elapsed_seconds": elapsed})
                    break

                status = self.operator.market_session_status()
                is_open = status.get("open", False)
                phase = status.get("status", "UNKNOWN")
                self._emit_status("market_status", {"open": is_open, "phase": phase})
                if is_open:
                    print(f"✅ Market is open (phase: {phase})")
                    break
                print(f"⏳ Market closed (phase: {phase}), waiting... ({int(elapsed)}s elapsed)")
                time.sleep(30)
        except Exception as e:
            print(f"⚠️ Error waiting for market: {e}")
            self._emit_status("market_wait_error", {"error": str(e)})

    # -------------------------------
    # Holdings review
    # -------------------------------
    def review_holdings(self) -> List[Dict[str, Any]]:
        self._emit_status("review_holdings_start", {})

        holdings = self._load_holdings()
        actions: List[Dict[str, Any]] = []

        if not holdings:
            self._emit_status(
                "review_holdings_complete",
                {"actions": [], "message": "No holdings to review"},
            )
            return []

        for holding in holdings:
            symbol = holding["symbol"]
            self._emit_status("reviewing_holding", {"symbol": symbol})

            desc = _tmpl(
                """Review swing holding for $symbol.

Current position:
$pos

Tasks:
1) Fetch recent news (last 24h) for $symbol
2) Check for negative shocks (downgrades, earnings miss, issues)
3) Check if +1R achieved (current price vs entry/target)
4) Recommend one: HOLD | SQUARE-OFF | TRAIL-TO-BE

Return JSON only:
{"recommendation":"HOLD|SQUARE-OFF|TRAIL-TO-BE","notes":"..."}
""",
                symbol=symbol,
                pos=holding,
            )

            task = Task(
                description=desc, agent=self.agents["monitor"], expected_output="JSON"
            )

            crew = Crew(
                agents=[self.agents["monitor"]],
                tasks=[task],
                process=Process.sequential,
                verbose=False,
            )

            result = str(crew.kickoff()).strip()

            try:
                parsed = json.loads(result) if result.startswith("{") else {}
                rec = (parsed.get("recommendation") or "").upper()
                if not rec:
                    up = result.upper()
                    if "SQUARE-OFF" in up:
                        rec = "SQUARE-OFF"
                    elif "TRAIL" in up or "BREAKEVEN" in up or "BE" in up:
                        rec = "TRAIL-TO-BE"
                    else:
                        rec = "HOLD"

                action = {
                    "symbol": symbol,
                    "action": rec.replace("-", "_").lower(),
                    "reason": parsed.get("notes") or result,
                    "timestamp": datetime.now(IST).isoformat(),
                }

                if rec == "SQUARE-OFF" and self.live and self.operator:
                    try:
                        exec_res = self.operator.square_off(symbol=symbol, live=True)

                        # Record exit in trade tracker if successful
                        if self.tracker and exec_res.get("status") == "ok":
                            try:
                                # Get current price for P&L calculation
                                current_price = None
                                if self.tech:
                                    px, _ = self.tech.ltp(holding.get("instrument_key", ""))
                                    current_price = float(px) if px else None

                                if current_price:
                                    pnl_record = self.tracker.record_exit(
                                        symbol=symbol,
                                        exit_price=current_price,
                                        exit_reason="SWING_REVIEW_RECOMMENDATION",
                                        order_id=exec_res.get("order_id"),
                                    )
                                    action["pnl_record"] = pnl_record
                                    print(f"💰 P&L recorded: {symbol} → ₹{pnl_record.get('net_pnl', 0):.2f}")
                            except Exception as e:
                                print(f"⚠️ Error recording exit P&L: {e}")

                    except Exception as e:
                        exec_res = {"ok": False, "error": str(e)}
                    action["execution"] = exec_res

                actions.append(action)
                self._emit_status("holding_action", action)

            except Exception as e:
                self._log_incident(
                    {
                        "type": "holding_review_error",
                        "symbol": symbol,
                        "error": str(e),
                        "raw": result,
                    }
                )

        self._emit_status("review_holdings_complete", {"actions": actions})
        return actions

    # -------------------------------
    # Intraday square-off (time-based)
    # -------------------------------
    def square_off_intraday_positions(self) -> List[Dict[str, Any]]:
        """
        Square off all intraday positions before market close.
        Default time: 3:10 PM IST (configurable via INTRADAY_SQUARE_OFF_TIME env var)
        Call this method during the trading session after the square-off time.
        """
        now = datetime.now(IST)

        # FIXED: Make square-off time configurable (default 3:10 PM)
        square_off_hour = int(os.environ.get("INTRADAY_SQUARE_OFF_HOUR", "15"))
        square_off_minute = int(os.environ.get("INTRADAY_SQUARE_OFF_MINUTE", "10"))
        square_off_time = now.replace(hour=square_off_hour, minute=square_off_minute, second=0, microsecond=0)

        # Only square off after configured time
        if now < square_off_time:
            return []

        print(f"\n⏰ Time: {now.strftime('%H:%M:%S')} - Squaring off intraday positions...")
        self._emit_status("intraday_square_off_start", {"time": now.isoformat()})

        squared_positions = []

        if not self.operator:
            print("⚠️ Operator not available, cannot square off")
            return []

        try:
            # Get current positions
            pos_data = self.operator.get_positions(include_closed=False)
            positions = pos_data.get("positions", [])

            intraday_positions = [
                p for p in positions
                if p.get("product", "").upper() == "I" and int(p.get("quantity", 0) or 0) != 0
            ]

            if not intraday_positions:
                print("✅ No intraday positions to square off")
                return []

            print(f"📊 Found {len(intraday_positions)} intraday position(s) to square off")

            for pos in intraday_positions:
                symbol = pos.get("tradingsymbol") or pos.get("symbol", "UNKNOWN")
                instrument_key = pos.get("instrument_token") or pos.get("instrument_key")

                try:
                    print(f"🔄 Squaring off {symbol}...")
                    result = self.operator.square_off(
                        symbol=symbol,
                        instrument_key=instrument_key,
                        live=self.live,
                    )

                    square_record = {
                        "symbol": symbol,
                        "result": result,
                        "time": now.isoformat(),
                    }

                    # Record exit in trade tracker if successful
                    if self.tracker and result.get("status") == "ok":
                        try:
                            # Get last traded price from position or fetch current
                            exit_price = float(pos.get("last_price") or 0)
                            if exit_price == 0 and self.tech:
                                px, _ = self.tech.ltp(instrument_key)
                                exit_price = float(px) if px else 0

                            if exit_price > 0:
                                pnl_record = self.tracker.record_exit(
                                    symbol=symbol,
                                    exit_price=exit_price,
                                    exit_reason="INTRADAY_AUTO_SQUARE_OFF",
                                    order_id=result.get("order_id"),
                                )
                                square_record["pnl_record"] = pnl_record
                                print(f"💰 P&L: {symbol} → ₹{pnl_record.get('net_pnl', 0):.2f} ({pnl_record.get('pnl_percent', 0):+.2f}%)")
                        except Exception as e:
                            print(f"⚠️ Error recording exit P&L for {symbol}: {e}")

                    squared_positions.append(square_record)

                    if result.get("status") == "ok":
                        print(f"✅ {symbol} squared off successfully")
                    else:
                        print(f"⚠️ {symbol} square-off failed: {result.get('message', 'unknown')}")

                except Exception as e:
                    print(f"❌ Error squaring off {symbol}: {e}")
                    squared_positions.append({
                        "symbol": symbol,
                        "error": str(e),
                        "time": now.isoformat(),
                    })

            self._emit_status("intraday_square_off_complete", {
                "count": len(squared_positions),
                "results": squared_positions,
            })

        except Exception as e:
            print(f"❌ Error during square-off: {e}")
            self._emit_status("intraday_square_off_error", {"error": str(e)})

        return squared_positions

    # -------------------------------
    # Decision (news + tech + market context)
    # -------------------------------
    def decide_trade(self, symbol: str, market_ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._emit_status("decide_start", {"symbol": symbol})

        # Validate and normalize symbol first
        is_valid, normalized_symbol, validation_msg = normalize_symbol(symbol)
        if not is_valid or not normalized_symbol:
            print(f"❌ Invalid symbol: {symbol} - {validation_msg}")
            decision = {
                "symbol": symbol,
                "direction": "SKIP",
                "reason": "invalid_symbol",
                "error": validation_msg,
                "timestamp": datetime.now(IST).isoformat(),
            }
            self._emit_status("decide_complete", decision)
            return decision

        # Use normalized symbol for all operations
        if normalized_symbol != symbol:
            print(f"📝 Symbol normalized: {symbol} → {normalized_symbol}")
        symbol = normalized_symbol

        if symbol in self.memory.get("blacklist", []):
            decision = {
                "symbol": symbol,
                "direction": "SKIP",
                "reason": "blacklisted",
                "timestamp": datetime.now(IST).isoformat(),
            }
            self._emit_status("decide_complete", decision)
            return decision

        news_desc = _tmpl(
            """Analyze news sentiment for $symbol (Mode $mode, Date $today).
Steps:
1) Fetch 1–2 days of news & broker calls
2) Score sentiment in [-1, +1] with time-decay (half-life ~18h)
3) Summarize key drivers

Return JSON only: {"news_score": <float>, "summary": "..."}
""",
            symbol=symbol,
            mode=self.mode,
            today=self.today,
        )

        tech_desc = _tmpl(
            """Analyze technicals for $symbol (Mode $mode, Date $today).
Use 30m (short-term) and Daily (trend). Extract RSI, EMA20/EMA50, MACD-hist, VWAP gap, ATR%.

Return JSON only:
{
  "ref_price": <float>,
  "indicators": {"rsi14":..,"ema20":..,"ema50":..,"atr_pct":..,"vwap_gap_pct":..},
  "tf": {"m30":{"trend":"UP|DOWN|FLAT","strength":0..1}, "d1":{"trend":"UP|DOWN|FLAT","strength":0..1}}
}
""",
            symbol=symbol,
            mode=self.mode,
            today=self.today,
        )

        mem_view = {
            k: self.memory[k] for k in ["w_news", "w_tech", "confidence_gate"]
        }

        # Prepare market context summary for decision agent
        market_summary = "Not available"
        if market_ctx:
            nifty = market_ctx.get("nifty", {})
            breadth = market_ctx.get("breadth", {})
            combined = market_ctx.get("combined_assessment", "UNKNOWN")

            market_summary = f"""
Nifty: {nifty.get('current_price', 0):.0f} ({nifty.get('change_percent', 0):+.2f}%)
Trend: {nifty.get('trend', 'UNKNOWN')} | Sentiment: {nifty.get('sentiment', 'NEUTRAL')}
Trading Bias: {nifty.get('trading_bias', 'SELECTIVE')}
"""
            if breadth and not breadth.get("error"):
                market_summary += f"""Breadth: {breadth.get('advance_percent', 50):.0f}% advancing, {breadth.get('above_ema_percent', 50):.0f}% above EMA20
Breadth Sentiment: {breadth.get('breadth_sentiment', 'NEUTRAL')}
"""
            market_summary += f"""Combined Assessment: {combined}
Agent Guidance: {nifty.get('agent_guidance', 'No guidance available')}"""

        decision_desc = _tmpl(
            """Make a trading decision for $symbol by synthesizing News + Technicals + Market Context.

Memory:
$memory

MARKET CONTEXT (Nifty & Breadth):
$market_context

IMPORTANT: Consider market context when making decisions:
- In BEARISH markets (Nifty down, weak breadth): Be more conservative, require higher confidence, prefer defensive stocks
- In BULLISH markets (Nifty up, strong breadth): Can be more aggressive with good setups
- In MIXED/NEUTRAL markets: Stock-specific analysis more important, use normal thresholds

CRITICAL: Determine STYLE (intraday vs swing) based on timeframe strengths.

STYLE SELECTION:
- If m30_strength ≥ 0.70 AND aligned with d1 → style="intraday"
- If d1_strength strong but m30 weaker → style="swing"
- If both weak → SKIP

CONFIDENCE CALCULATION (product-specific):

FOR INTRADAY:
- Base confidence = 0.70 × m30_strength + 0.30 × news_score
- Adjust for market: +0.05 if market bullish, -0.05 if bearish
- Gate: ≥0.60 (higher bar)

FOR SWING:
- Base confidence = 0.55 × d1_strength + 0.45 × news_score
- Adjust for market: +0.05 if market bullish, -0.05 if bearish
- Gate: ≥0.50

ALIGNMENT:
1) Both align (news + tech same direction) → use that direction
2) Conflict → require dominance:
   - news_score ≥ ±0.70 OR
   - tech_strength ≥ 0.75
3) Otherwise → SKIP

MARKET OVERRIDE:
- If market is STRONG_BEARISH and stock signal is BUY: increase confidence requirement to 0.70
- If market is STRONG_BULLISH and stock signal is BUY: can lower requirement to 0.55 (intraday) / 0.45 (swing)

Return JSON only (include style!):
{"direction":"BUY|SELL|SKIP","confidence":0..1,"style":"intraday|swing","rationale":"..."}
""",
            symbol=symbol,
            memory=mem_view,
            gate=self.memory.get("confidence_gate", 0.50),
            market_context=market_summary,
        )

        news_task = Task(
            description=news_desc,
            agent=self.agents["news"],
            expected_output="JSON",
        )
        tech_task = Task(
            description=tech_desc,
            agent=self.agents["technical"],
            expected_output="JSON",
        )
        decision_task = Task(
            description=decision_desc,
            agent=self.agents["lead"],
            expected_output="JSON",
            context=[news_task, tech_task],
        )

        crew = Crew(
            agents=[self.agents["news"], self.agents["technical"], self.agents["lead"]],
            tasks=[news_task, tech_task, decision_task],
            process=Process.sequential,
            verbose=self.crew_verbose,
        )

        self._emit_status("decision_analyzing", {"symbol": symbol})
        result = str(crew.kickoff()).strip()

        try:
            # Try to extract JSON from result
            if "{" in result:
                json_start = result.find("{")
                json_end = result.rfind("}") + 1
                if json_end > json_start:
                    result = result[json_start:json_end]

            parsed = json.loads(result)

            # CRITICAL SAFETY: Detect fabricated technical data
            # If LLM fails to call tools, it may fabricate realistic data instead
            if not validate_technical_data(parsed, agent_name="lead_coordinator"):
                print(f"🚨 FABRICATED DATA DETECTED! Rejecting decision for safety.")
                print(f"   This trade would be based on hallucinated technical analysis!")
                print(f"   Direction: SKIP (forced)")
                self._log_incident({
                    "type": "fabricated_data_detected",
                    "symbol": symbol,
                    "raw_decision": result[:500],
                    "timestamp": datetime.now(IST).isoformat(),
                })
                return {
                    "symbol": symbol,
                    "direction": "SKIP",
                    "confidence": 0.0,
                    "raw": result,
                    "error": "fabricated_data_detected",
                    "timestamp": datetime.now(IST).isoformat(),
                }

            direction = (parsed.get("direction") or "SKIP").upper()
            conf = parsed.get("confidence", None)

            decision = {
                "symbol": symbol,
                "direction": direction,
                "confidence": conf,
                "raw": result,
                "timestamp": datetime.now(IST).isoformat(),
            }
            self._emit_status("decide_complete", decision)
            return decision

        except Exception as e:
            # FIXED: DO NOT attempt to extract or execute trades on parse errors
            # Executing with unreliable/malformed data is too risky
            print(f"⚠️ Could not parse decision JSON - defaulting to SKIP for safety")
            print(f"   Parse error: {e}")
            print(f"   Raw result (first 300 chars): {result[:300]}...")

            self._log_incident(
                {
                    "type": "decision_parse_error",
                    "symbol": symbol,
                    "error": str(e),
                    "raw_result": result,
                    "action": "SKIP (safety default)",
                }
            )
            decision = {
                "symbol": symbol,
                "direction": "SKIP",
                "confidence": 0.0,
                "reason": "parse_error_safety_skip",
                "error": str(e),
                "raw": result,
                "timestamp": datetime.now(IST).isoformat(),
            }
            self._emit_status("decide_complete", decision)
            return decision

    # -------------------------------
    # Sizing + Execution
    # -------------------------------
    def _fresh_snapshot(self, symbol: str, days: int = 7) -> Dict[str, Any]:
        if not self.tech:
            raise RuntimeError("Technical client not initialized")
        try:
            return self.tech.snapshot(symbol, days=days)
        except Exception as e:
            raise RuntimeError(f"Failed to fetch snapshot for {symbol}: {e}")

    def size_and_execute(
        self, symbol: str, direction: str, confidence: float
    ) -> Dict[str, Any]:
        if direction == "SKIP":
            return {
                "symbol": symbol,
                "status": "skipped",
                "reason": "direction_skip",
            }

        self._emit_status("sizing_start", {"symbol": symbol, "direction": direction})

        # 1) Fresh technical snapshot (for ATR etc.)
        try:
            tech_snap = self._fresh_snapshot(symbol, days=7)
            print(f"✅ Snapshot {symbol}: price={tech_snap.get('current_price')}")
        except Exception as e:
            print(f"❌ Snapshot error for {symbol}: {e}")
            self._emit_status("snapshot_error", {"symbol": symbol, "error": str(e)})
            return {
                "symbol": symbol,
                "status": "skipped",
                "reason": "snapshot_error",
                "error": str(e),
            }

        # 2) 🆕 ENTRY VALIDATION - Check if NOW is a good time to enter
        self._emit_status("entry_validation_start", {"symbol": symbol, "direction": direction})

        entry_validation_desc = _tmpl(
            """Entry quality check for $symbol ($direction, conf=$confidence).

Technical Data: $snapshot

SIMPLIFIED SCORING (start at 60, trust decision agent):
- Base: 60 points
- RSI 40-75: +10
- RSI >80 or <20: -20
- Price >3% from VWAP: -10
- Good time (9:20-11:30, 14:00-14:45): +10
- Bad time (last 10 min): -15

RULES:
- Score ≥60: ENTER_NOW (allow trade)
- Score 40-59: WATCHLIST
- Score <40: SKIP

Return ONLY this JSON (NO arrays, NO nested objects):
{{
  "entry_decision": "ENTER_NOW",
  "entry_quality_score": 70,
  "reason": "RSI healthy timing good"
}}
""",
            symbol=symbol,
            direction=direction,
            confidence=f"{confidence:.3f}",
            snapshot=tech_snap,
        )

        entry_task = Task(
            description=entry_validation_desc,
            agent=self.agents["entry_validator"],
            expected_output="JSON",
        )

        entry_crew = Crew(
            agents=[self.agents["entry_validator"]],
            tasks=[entry_task],
            process=Process.sequential,
            verbose=self.crew_verbose,
        )

        entry_result_str = str(entry_crew.kickoff()).strip()
        self._emit_status("entry_validation_complete", {"symbol": symbol, "result": entry_result_str})

        try:
            # Parse entry validation result with improved error handling
            entry_validation = safe_parse_json(
                entry_result_str,
                fallback={
                    "entry_decision": "ENTER_NOW",
                    "entry_quality_score": 65,
                    "reason": "Validator failed, trusting decision agent"
                }
            )

            # Use safer default: SKIP if validation fails instead of ENTER_NOW
            if entry_validation.get("entry_decision") is None:
                print(f"⚠️ Entry validation malformed JSON, defaulting to SKIP for safety")
                entry_validation = {
                    "entry_decision": "SKIP",
                    "entry_quality_score": 0,
                    "reason": "Validator failed - cannot assess entry quality safely"
                }

            entry_decision = (entry_validation.get("entry_decision") or "SKIP").upper()
            entry_quality = int(entry_validation.get("entry_quality_score") or 0)
            entry_reason = entry_validation.get("reason", "")

            print(f"📊 Entry Quality: {entry_quality}/100 → {entry_decision}")
            print(f"   Reason: {entry_reason}")

            if entry_decision == "SKIP":
                return {
                    "symbol": symbol,
                    "status": "skipped",
                    "reason": "entry_quality_too_low",
                    "entry_quality_score": entry_quality,
                    "entry_reason": entry_reason,
                }

            elif entry_decision == "WATCHLIST":
                # Add to watchlist for monitoring
                print(f"📋 Adding {symbol} to watchlist (quality: {entry_quality})")
                wait_for = entry_validation.get("wait_for", "better entry")

                # Import and use watchlist manager
                try:
                    from watchlist_manager import get_watchlist_manager
                    wm = get_watchlist_manager()
                    wm.add_to_intraday_watchlist(
                        symbol=symbol,
                        signal=direction,
                        reason=entry_reason,
                        entry_target=None,  # Could parse from wait_for
                        current_price=tech_snap.get("current_price"),
                        confidence=confidence,
                        entry_quality=entry_quality,
                        setup_type="pending_entry",
                        technical_data=tech_snap,
                    )
                    print(f"✅ {symbol} added to watchlist: {wait_for}")
                except Exception as e:
                    print(f"⚠️ Error adding to watchlist: {e}")

                return {
                    "symbol": symbol,
                    "status": "watchlisted",
                    "reason": "waiting_for_better_entry",
                    "entry_quality_score": entry_quality,
                    "wait_for": wait_for,
                    "entry_reason": entry_reason,
                }

            # If ENTER_NOW, continue to sizing & execution
            print(f"✅ Entry validated - proceeding with execution")

        except Exception as e:
            print(f"⚠️ Entry validation parse error: {e}")
            # If validation fails, default to cautious (skip)
            return {
                "symbol": symbol,
                "status": "skipped",
                "reason": "entry_validation_parse_error",
                "error": str(e),
            }

        # 3) Ask Risk agent for a plan
        risk_desc = _tmpl(
            """Build position plan for $symbol.

Inputs:
- Direction: $direction
- Confidence: $confidence
- Technical Snapshot: $snapshot

CRITICAL INSTRUCTIONS:
1) Call get_funds_tool FIRST to get ACTUAL available capital (do NOT assume or hallucinate amounts)
2) Use current_price from snapshot as entry price
3) Calculate stop_loss from ATR:
   - For intraday: stop_loss = entry × (1 - atr_pct/100 × 0.9) for BUY
   - For swing: stop_loss = entry × (1 - atr_pct/100 × 1.8) for BUY
4) Call calculate_max_quantity_tool with the ACTUAL available_margin from step 1
5) Calculate target for minimum R:R:
   - Intraday: target = entry + (entry - stop_loss) × 1.3
   - Swing: target = entry + (entry - stop_loss) × 1.6
6) Return a FLAT JSON object (NO NESTING!)

REQUIRED OUTPUT FORMAT (must be flat, all fields at root level):
{
  "symbol": "$symbol",
  "direction": "$direction",
  "side": "$direction",
  "style": "intraday",
  "product": "I",
  "qty": <integer from calculate_max_quantity_tool>,
  "entry": <float from snapshot>,
  "stop_loss": <float calculated above>,
  "target": <float calculated above>,
  "order_type": "MARKET",
  "rr_ratio": <float>,
  "rationale": "Brief 1-line explanation"
}

IMPORTANT:
- Do NOT nest inside "final_choice", "plan", "intraday", or "swing" keys
- Do NOT include alternative plans in the response
- If qty < 1 → return {"decision":"SKIP","reason":"insufficient_capital"}
- Use get_funds_tool to get real available margin (not hardcoded values!)
""",
            symbol=symbol,
            direction=direction,
            confidence=f"{confidence:.3f}",
            snapshot=tech_snap,
        )

        risk_task = Task(
            description=risk_desc,
            agent=self.agents["risk"],
            expected_output="JSON",
        )

        risk_crew = Crew(
            agents=[self.agents["risk"]],
            tasks=[risk_task],
            process=Process.sequential,
            verbose=self.crew_verbose,
        )

        plan_str = str(risk_crew.kickoff()).strip()
        self._emit_status("sizing_complete", {"symbol": symbol, "plan": plan_str})

        # 3) Parse & validate risk plan
        try:
            plan_obj = (
                json.loads(plan_str) if plan_str.lstrip().startswith("{") else {}
            )
        except Exception as e:
            self._log_incident(
                {
                    "type": "risk_plan_parse_error",
                    "symbol": symbol,
                    "error": str(e),
                    "raw_plan": plan_str,
                }
            )
            return {
                "symbol": symbol,
                "status": "skipped",
                "reason": "risk_plan_parse_error",
            }

        # Explicit SKIP from risk agent
        if (plan_obj.get("decision") or "").upper() == "SKIP":
            return {
                "symbol": symbol,
                "status": "skipped",
                "reason": plan_obj.get("reason", "infeasible"),
            }

        # FIXED: Validate risk plan structure more strictly
        # Risk agent should return flat JSON with: qty, entry, stop_loss, target, etc.
        # If it's nested, try to unwrap but log a warning
        chosen = None

        # First check if it's a flat structure with required fields
        if "qty" in plan_obj or "entry" in plan_obj:
            chosen = plan_obj  # Flat structure - preferred format
        else:
            # Try to unwrap nested structures (but log warning)
            for key in ("final_choice", "plan", "intraday", "swing"):
                v = plan_obj.get(key)
                if isinstance(v, dict):
                    print(f"⚠️ Risk agent returned nested structure under '{key}' - please fix agent to return flat JSON")
                    chosen = v
                    break

            if chosen is None:
                print(f"⚠️ Risk agent returned unexpected structure, treating as flat")
                chosen = plan_obj

        # Ensure basic fields (also provide 'side' alias for executor)
        chosen.setdefault("symbol", symbol)
        chosen.setdefault("direction", direction)
        chosen.setdefault("side", direction)

        qty = int(chosen.get("qty") or 0)
        entry = chosen.get("entry")
        stop_loss = chosen.get("stop_loss") or chosen.get("stop")
        stop_loss_pct = chosen.get("stop_loss_pct")

        def _is_num(x: Any) -> bool:
            return isinstance(x, (int, float))

        # NEW: allow either absolute stop_loss OR stop_loss_pct (mandatory SL policy)
        if qty < 1 or not _is_num(entry) or (
            stop_loss is None and stop_loss_pct is None
        ):
            self._log_incident(
                {
                    "type": "risk_plan_invalid",
                    "symbol": symbol,
                    "raw_plan": plan_str,
                    "plan_obj": plan_obj,
                }
            )
            return {
                "symbol": symbol,
                "status": "skipped",
                "reason": "invalid_risk_plan",
            }

        # FIXED: Validate SL is in correct direction relative to entry price
        if stop_loss is not None and _is_num(stop_loss):
            entry_price = float(entry)
            sl_price = float(stop_loss)

            if direction == "BUY" and sl_price >= entry_price:
                print(f"⚠️ Invalid SL for BUY: stop_loss ({sl_price}) must be < entry ({entry_price})")
                return {
                    "symbol": symbol,
                    "status": "skipped",
                    "reason": "invalid_stop_loss_direction",
                    "detail": f"BUY stop_loss {sl_price} >= entry {entry_price}"
                }
            elif direction == "SELL" and sl_price <= entry_price:
                print(f"⚠️ Invalid SL for SELL: stop_loss ({sl_price}) must be > entry ({entry_price})")
                return {
                    "symbol": symbol,
                    "status": "skipped",
                    "reason": "invalid_stop_loss_direction",
                    "detail": f"SELL stop_loss {sl_price} <= entry {entry_price}"
                }

        # Clean plan JSON that we pass to executor
        cleaned_plan_str = json.dumps(chosen, ensure_ascii=False)

        # 4) Executor agent: place order using cleaned plan
        exec_desc = _tmpl(
            """Execute trade for $symbol.

CRITICAL INSTRUCTIONS:
- For INTRADAY trades (product="I" or style="intraday"): Use place_intraday_bracket_tool
- For SWING trades (product="D" or style="swing"): Use place_order_tool
- MUST pass "live": $live in the tool call
- Stop-loss is MANDATORY: provide stop_loss OR stop_loss_pct
- target/target_pct are OPTIONAL

Input (Position Plan JSON from previous step):
$plan

STEPS:
1) Check if market is open using get_market_status_tool (only if live=$live)
2) Extract from plan: symbol, side, qty, product, order_type, entry, stop_loss, target
3) Call the appropriate tool:
   - If product="I": place_intraday_bracket_tool with {
       "symbol": "$symbol",
       "side": "BUY|SELL",
       "qty": <from plan>,
       "product": "I",
       "order_type": "MARKET",
       "stop_loss": <from plan>,
       "target": <from plan>,
       "live": $live
     }
   - If product="D": place_order_tool with same payload
4) Return the tool's JSON result EXACTLY as-is

DO NOT fabricate order IDs or responses. Only return what the tool actually returns.
""",
            symbol=symbol,
            live=str(self.live).lower(),
            mode=self.mode,
            plan=cleaned_plan_str,
        )

        exec_task = Task(
            description=exec_desc,
            agent=self.agents["executor"],
            expected_output="JSON",
        )

        exec_crew = Crew(
            agents=[self.agents["executor"]],
            tasks=[exec_task],
            process=Process.sequential,
            verbose=self.crew_verbose,
        )

        exec_result = str(exec_crew.kickoff()).strip()

        # Parse execution result to extract trade details with improved error handling
        exec_data = safe_parse_json(exec_result, fallback=None, log_failures=True)
        if exec_data is None:
            print(f"⚠️ Could not parse execution result for trade tracking")

        # FIXED: Record trade entry in tracker using plan data (don't depend on exec_data parsing)
        # This ensures position is tracked even if execution result JSON parsing fails
        trade_record = None
        if self.tracker:
            try:
                # Get all required data from the plan (chosen) - we already have this!
                entry_price = float(chosen.get("entry", 0))
                stop_loss = float(chosen.get("stop_loss") or 0) if chosen.get("stop_loss") else None
                target = float(chosen.get("target") or 0) if chosen.get("target") else None

                # Try to get order_id from exec_data if available, otherwise None
                order_id = None
                if exec_data:
                    order_info = exec_data.get("order") or exec_data.get("entry") or {}
                    order_id = order_info.get("order_id")

                # Record entry if we have valid data
                if entry_price > 0 and qty > 0:
                    trade_record = self.tracker.record_entry(
                        symbol=symbol,
                        side=direction,
                        quantity=qty,
                        entry_price=entry_price,
                        product=chosen.get("product", "I"),
                        order_id=order_id,
                        stop_loss=stop_loss,
                        target=target,
                        strategy=chosen.get("strategy", "ai_decision"),
                        confidence=confidence,
                        tags=[self.mode, chosen.get("style", "unknown")],
                        metadata={
                            "rationale": chosen.get("rationale"),
                            "rr_ratio": chosen.get("rr_ratio"),
                        }
                    )
                    print(f"✅ Trade recorded in tracker: {trade_record.get('trade_id')}")
                else:
                    print(f"⚠️ Cannot track trade: invalid entry_price={entry_price} or qty={qty}")
            except Exception as e:
                # CRITICAL: If tracking fails, log loudly - this is a serious problem!
                print(f"🚨 CRITICAL ERROR recording trade entry: {e}")
                print(f"   Position may be open but UNTRACKED - check manually!")
                import traceback
                traceback.print_exc()

        result = {
            "symbol": symbol,
            "direction": direction,
            "plan": cleaned_plan_str,
            "execution": exec_result,
            "timestamp": datetime.now(IST).isoformat(),
            "trade_record": trade_record,  # Add tracker record to result
        }
        self._emit_status("execution_complete", result)
        self._append_ledger(result)
        return result

    # -------------------------------
    # Full cycle
    # -------------------------------
    def run_decision_cycle(self, symbols: Sequence[str]) -> Dict[str, Any]:
        # Validate and normalize all symbols upfront
        print(f"\n🔍 Validating {len(symbols)} symbols...")
        valid_symbols, validation_errors = validate_symbol_list(list(symbols))

        if validation_errors:
            print(f"⚠️  Symbol validation warnings:")
            for err in validation_errors:
                print(f"   {err}")

        if not valid_symbols:
            print(f"❌ No valid symbols to analyze!")
            return {
                "date": self.today,
                "mode": self.mode,
                "error": "no_valid_symbols",
                "validation_errors": validation_errors,
                "timestamp": datetime.now(IST).isoformat(),
            }

        print(f"✅ Proceeding with {len(valid_symbols)} validated symbols: {', '.join(valid_symbols)}")

        self._emit_status(
            "cycle_start",
            {"symbols": valid_symbols, "mode": self.mode, "date": self.today},
        )
        self._wait_until_open_if_needed()

        cycle_results: Dict[str, Any] = {
            "date": self.today,
            "mode": self.mode,
            "live": self.live,
            "start_time": datetime.now(IST).isoformat(),
            "holdings_review": [],
            "decisions": [],
            "executions": [],
            "capital_tracking": {
                "initial_capital": 0.0,
                "final_capital": 0.0,
                "used_capital": 0.0,
                "max_positions": int(os.environ.get("MAX_POSITIONS", "5")),
            },
        }

        # Get initial capital and wallet status
        try:
            if self.money_manager:
                # Get comprehensive wallet status
                wallet_status = self.money_manager.get_wallet_status()
                cycle_results["wallet_status"] = wallet_status

                initial_capital = wallet_status.get("available_capital", 0)
                cycle_results["capital_tracking"]["initial_capital"] = initial_capital

                print(f"\n💰 Wallet Status:")
                print(f"   Total Capital: ₹{wallet_status.get('total_capital', 0):,.2f}")
                print(f"   Available: ₹{wallet_status.get('available_capital', 0):,.2f}")
                print(f"   Used: ₹{wallet_status.get('used_capital', 0):,.2f} ({wallet_status.get('capital_usage_pct', 0):.1f}%)")
                print(f"   Intraday Allocation: ₹{wallet_status.get('intraday_allocation', 0):,.2f}")
                print(f"   Swing Allocation: ₹{wallet_status.get('swing_allocation', 0):,.2f}")
                print(f"   Daily P&L: ₹{wallet_status.get('daily_pnl', 0):,.2f}")
                print(f"   Daily Trades: {wallet_status.get('daily_trades', 0)}/{wallet_status.get('max_daily_trades', 0)}")

                if not wallet_status.get("can_trade"):
                    print(f"\n⚠️ TRADING BLOCKED: {', '.join(wallet_status.get('blocking_reasons', []))}")
                    cycle_results["trading_blocked"] = True
                    cycle_results["blocking_reasons"] = wallet_status.get("blocking_reasons", [])
                else:
                    print(f"\n✅ Trading allowed - All limits OK")

            elif self.operator:
                funds = self.operator.get_funds()
                initial_capital = float(
                    (funds.get("equity") or {}).get("available_margin", 0) or 0
                )
                cycle_results["capital_tracking"]["initial_capital"] = initial_capital
                print(f"💰 Starting capital: ₹{initial_capital:,.2f}")
            else:
                initial_capital = 0.0
                print("⚠️ Operator not available, capital tracking disabled")
        except Exception as e:
            print(f"⚠️ Error fetching wallet status: {e}")
            initial_capital = 0.0

        # Get market context (Nifty + breadth)
        market_ctx = None
        if self.market_context:
            try:
                print("\n📊 Fetching market context...")
                # Get complete context with breadth analysis on provided symbols
                market_ctx = self.market_context.get_complete_market_context(symbols=valid_symbols)

                nifty = market_ctx.get("nifty", {})
                breadth = market_ctx.get("breadth", {})

                print(f"\n🔍 Market Context:")
                print(f"   Nifty: {nifty.get('current_price', 0):.2f} ({nifty.get('change_percent', 0):+.2f}%)")
                print(f"   Trend: {nifty.get('trend', 'UNKNOWN')} | Sentiment: {nifty.get('sentiment', 'NEUTRAL')}")
                print(f"   Trading Bias: {nifty.get('trading_bias', 'SELECTIVE')}")

                if breadth and not breadth.get("error"):
                    print(f"\n📈 Market Breadth ({breadth.get('stocks_analyzed', 0)} stocks):")
                    print(f"   Advancing: {breadth.get('advance_percent', 0):.1f}% | Declining: {breadth.get('decline_percent', 0):.1f}%")
                    print(f"   Above EMA20: {breadth.get('above_ema_percent', 0):.1f}%")
                    print(f"   Breadth Sentiment: {breadth.get('breadth_sentiment', 'NEUTRAL')}")

                if market_ctx.get("combined_assessment"):
                    print(f"\n💡 Combined Assessment: {market_ctx.get('combined_assessment')}")
                    print(f"   {market_ctx.get('recommendation', '')}")

                cycle_results["market_context"] = market_ctx
                self._emit_status("market_context_loaded", market_ctx)

            except Exception as e:
                print(f"⚠️ Error fetching market context: {e}")
                market_ctx = None

        holdings_actions = self.review_holdings()
        cycle_results["holdings_review"] = holdings_actions

        # Square off intraday positions if near market close (after 3:10 PM)
        intraday_square_offs = self.square_off_intraday_positions()
        if intraday_square_offs:
            cycle_results["intraday_square_offs"] = intraday_square_offs

        # Track positions and capital usage
        executed_positions = 0
        max_positions = cycle_results["capital_tracking"]["max_positions"]
        capital_utilization_limit = float(os.environ.get("MAX_CAPITAL_UTILIZATION", "0.90"))

        # PHASE 1: ANALYZE ALL SYMBOLS FIRST (Quality over Quantity)
        print(f"\n{'='*80}")
        print(f"📊 PHASE 1: Analyzing ALL {len(valid_symbols)} symbols to find best opportunities...")
        print(f"{'='*80}\n")

        all_decisions = []
        for idx, symbol in enumerate(valid_symbols, 1):
            try:
                print(f"🔍 Analyzing {symbol} ({idx}/{len(valid_symbols)})...")
                decision = self.decide_trade(symbol, market_ctx=market_ctx)
                cycle_results["decisions"].append(decision)

                # Only keep BUY/SELL decisions for ranking
                if decision.get("direction") in ("BUY", "SELL"):
                    confidence = float(decision.get("confidence") or 0.0)
                    all_decisions.append({
                        "symbol": symbol,
                        "direction": decision["direction"],
                        "confidence": confidence,
                        "decision_data": decision,
                    })
                    print(f"   ✅ {decision['direction']} signal with {confidence:.3f} confidence")
                else:
                    print(f"   ⏭️  SKIP - {decision.get('reason', 'no signal')}")

                time.sleep(0.3)  # Reduced delay for analysis phase

            except Exception as e:
                print(f"❌ Error analyzing {symbol}: {e}")
                self._log_incident({
                    "type": "symbol_analysis_error",
                    "symbol": symbol,
                    "error": str(e),
                })
                cycle_results["decisions"].append({
                    "symbol": symbol,
                    "direction": "ERROR",
                    "error": str(e),
                })

        # PHASE 2: RANK AND SELECT BEST OPPORTUNITIES
        print(f"\n{'='*80}")
        print(f"🎯 PHASE 2: Ranking opportunities (Quality over Quantity)")
        print(f"{'='*80}\n")

        if not all_decisions:
            print("⚠️ No trading opportunities found in any symbols")
            self._emit_status("no_opportunities", {"analyzed": len(valid_symbols)})
        else:
            # Sort by confidence (highest first)
            all_decisions.sort(key=lambda x: x["confidence"], reverse=True)

            print(f"📋 Found {len(all_decisions)} trading opportunities:")
            for idx, opp in enumerate(all_decisions, 1):
                print(f"   {idx}. {opp['symbol']}: {opp['direction']} @ {opp['confidence']:.3f} confidence")

            # Select top N based on available positions
            max_to_execute = min(max_positions, len(all_decisions))
            selected = all_decisions[:max_to_execute]

            print(f"\n✨ SELECTED TOP {len(selected)} BEST OPPORTUNITIES:")
            for opp in selected:
                print(f"   • {opp['symbol']}: {opp['direction']} @ {opp['confidence']:.3f}")

            # PHASE 3: EXECUTE WITH FRESH PRICES
            print(f"\n{'='*80}")
            print(f"🚀 PHASE 3: Executing {len(selected)} trades with CURRENT prices")
            print(f"{'='*80}\n")

            for opp_idx, opp in enumerate(selected, 1):
                try:
                    symbol = opp["symbol"]
                    direction = opp["direction"]
                    confidence = opp["confidence"]

                    # Check capital availability BEFORE execution
                    if self.operator and initial_capital > 0:
                        try:
                            current_funds = self.operator.get_funds()
                            current_available = float(
                                (current_funds.get("equity") or {}).get("available_margin", 0) or 0
                            )
                            used_capital = initial_capital - current_available
                            utilization = used_capital / initial_capital if initial_capital > 0 else 0

                            print(
                                f"💰 Capital: ₹{current_available:,.2f} available "
                                f"(used: ₹{used_capital:,.2f}, {utilization:.1%})"
                            )

                            if utilization >= capital_utilization_limit:
                                print(
                                    f"⚠️ Capital utilization {utilization:.1%} >= {capital_utilization_limit:.1%}, "
                                    f"stopping remaining trades"
                                )
                                self._emit_status(
                                    "capital_exhausted",
                                    {"utilization": utilization, "limit": capital_utilization_limit},
                                )
                                break

                            if current_available < 1000:
                                print(f"⚠️ Insufficient capital (₹{current_available:,.2f} < ₹1,000), stopping")
                                break

                        except Exception as e:
                            print(f"⚠️ Error checking capital: {e}")

                    print(f"\n{'─'*80}")
                    print(f"🎯 Executing Trade {opp_idx}/{len(selected)}: {symbol} {direction}")
                    print(f"{'─'*80}")

                    # Execute with size_and_execute (which gets FRESH prices)
                    execution = self.size_and_execute(symbol, direction, confidence)
                    cycle_results["executions"].append(execution)

                    # Track executed positions
                    if execution.get("status") not in ("skipped", "error"):
                        exec_data = execution.get("execution")
                        if isinstance(exec_data, str):
                            exec_data = safe_parse_json(exec_data, fallback=None, log_failures=False)
                        if isinstance(exec_data, dict) and exec_data.get("status") in ("success", "ok"):
                            executed_positions += 1
                            print(f"✅ Position {executed_positions} opened successfully")

                    time.sleep(0.5)  # Brief pause between executions

                except Exception as e:
                    print(f"❌ Error executing {symbol}: {e}")
                    self._log_incident({
                        "type": "symbol_execution_error",
                        "symbol": symbol,
                        "error": str(e),
                    })
                    cycle_results["executions"].append({
                        "symbol": symbol,
                        "status": "error",
                        "error": str(e),
                    })

        # Final capital tracking
        if self.operator and initial_capital > 0:
            try:
                final_funds = self.operator.get_funds()
                final_capital = float(
                    (final_funds.get("equity") or {}).get("available_margin", 0) or 0
                )
                cycle_results["capital_tracking"]["final_capital"] = final_capital
                cycle_results["capital_tracking"]["used_capital"] = (
                    initial_capital - final_capital
                )
                print(f"\n💰 Final capital: ₹{final_capital:,.2f} (used: ₹{initial_capital - final_capital:,.2f})")
            except Exception as e:
                print(f"⚠️ Error fetching final capital: {e}")

        # Get P&L summary for today
        if self.tracker:
            try:
                daily_pnl = self.tracker.get_daily_pnl(self.today)
                cycle_results["pnl_summary"] = daily_pnl

                print(f"\n📊 Today's P&L Summary ({self.today}):")
                print(f"   Total Trades: {daily_pnl.get('total_trades', 0)}")
                print(f"   Gross P&L: ₹{daily_pnl.get('gross_pnl', 0):,.2f}")
                print(f"   Charges: ₹{daily_pnl.get('charges', 0):,.2f}")
                print(f"   Net P&L: ₹{daily_pnl.get('net_pnl', 0):,.2f}")
                print(f"   Win Rate: {daily_pnl.get('win_rate', 0):.1f}%")
                print(f"   Winners: {daily_pnl.get('winning_trades', 0)} | Losers: {daily_pnl.get('losing_trades', 0)}")

                # Get overall trading statistics
                stats = self.tracker.get_trade_statistics()
                if not stats.get("error"):
                    cycle_results["trading_statistics"] = stats
                    print(f"\n📈 Overall Statistics:")
                    print(f"   Total Trades: {stats.get('total_trades', 0)}")
                    print(f"   Overall Win Rate: {stats.get('win_rate', 0):.1f}%")
                    print(f"   Total P&L: ₹{stats.get('total_pnl', 0):,.2f}")
                    print(f"   Avg P&L/Trade: ₹{stats.get('average_pnl_per_trade', 0):,.2f}")
                    print(f"   Profit Factor: {stats.get('profit_factor', 0):.2f}")

            except Exception as e:
                print(f"⚠️ Error fetching P&L summary: {e}")

        # Check open positions for SL/target hits
        if self.position_monitor:
            try:
                print(f"\n🔍 Checking open positions for SL/target hits...")
                position_check = self.position_monitor.check_positions(live=self.live)

                cycle_results["position_check"] = position_check

                if position_check.get("actions_taken"):
                    print(f"\n⚡ Position Actions:")
                    for action in position_check["actions_taken"]:
                        symbol = action.get("symbol")
                        reason = action.get("exit_reason")
                        pnl_record = action.get("pnl_record", {})
                        net_pnl = pnl_record.get("net_pnl", 0)

                        print(f"   {symbol}: {reason} → P&L: ₹{net_pnl:,.2f}")

                        # Update money manager with this trade result
                        if self.money_manager and pnl_record:
                            self.money_manager.record_trade_result(
                                net_pnl=net_pnl,
                                product=pnl_record.get("product", "I")
                            )
                else:
                    print(f"   No position exits triggered")

                # Show position summary
                pos_summary = self.position_monitor.get_position_summary()
                if pos_summary.get("total_positions", 0) > 0:
                    print(f"\n📊 Open Positions: {pos_summary.get('total_positions', 0)}")
                    print(f"   Intraday: {pos_summary.get('intraday_count', 0)} | Swing: {pos_summary.get('swing_count', 0)}")
                    print(f"   Unrealized P&L: ₹{pos_summary.get('total_unrealized_pnl', 0):,.2f}")

            except Exception as e:
                print(f"⚠️ Error checking positions: {e}")

        cycle_results["end_time"] = datetime.now(IST).isoformat()
        self._save_decisions(cycle_results)
        self._emit_status("cycle_complete", cycle_results)
        return cycle_results

    # -------------------------------
    # Learning mode - Enhanced with Learning Engine
    # -------------------------------
    def run_learning_mode(self, days: int = 30) -> Dict[str, Any]:
        """
        Run comprehensive learning analysis on recent trades.

        Analyzes what worked, what didn't, and adjusts parameters.
        This makes the system better every day!

        Args:
            days: Number of days of history to analyze

        Returns:
            Learning analysis with recommendations and adjustments
        """
        self._emit_status("learning_start", {"days": days})

        print(f"\n🧠 Starting Learning Mode (analyzing last {days} days)...")
        print("=" * 80)

        result = {
            "status": "complete",
            "timestamp": datetime.now(IST).isoformat(),
        }

        # 1. Get current learning state
        if self.learning_engine:
            try:
                learning_summary = self.learning_engine.get_learning_summary()
                result["current_state"] = learning_summary

                print(f"\n📚 Current Learning State:")
                print(f"   Last Analysis: {learning_summary.get('last_analysis', 'Never')}")
                print(f"   Trades Analyzed: {learning_summary.get('total_trades_analyzed', 0)}")
                print(f"   Confidence Threshold: {learning_summary.get('confidence_threshold', 0.60):.2f}")
                print(f"   Patterns Learned: {learning_summary.get('patterns_learned', {}).get('winning', 0)} winning, {learning_summary.get('patterns_learned', {}).get('losing', 0)} losing")

            except Exception as e:
                print(f"⚠️ Error getting learning state: {e}")

        # 2. Analyze trade history
        if self.learning_engine:
            try:
                print(f"\n🔍 Analyzing trade history...")
                analysis = self.learning_engine.analyze_trade_history(days=days)

                result["analysis"] = analysis

                if analysis.get("status") == "insufficient_data":
                    print(f"\n⚠️ {analysis.get('message')}")
                    return result

                # Display key metrics
                print(f"\n📊 Performance Metrics:")
                print(f"   Total Trades: {analysis.get('total_trades', 0)}")
                print(f"   Winners: {analysis.get('winners', 0)} | Losers: {analysis.get('losers', 0)} | Breakeven: {analysis.get('breakeven', 0)}")
                print(f"   Win Rate: {analysis.get('win_rate', 0):.1f}%")
                print(f"   Total P&L: ₹{analysis.get('total_pnl', 0):,.2f}")
                print(f"   Avg Win: ₹{analysis.get('avg_win', 0):,.2f}")
                print(f"   Avg Loss: ₹{analysis.get('avg_loss', 0):,.2f}")
                print(f"   Profit Factor: {analysis.get('profit_factor', 0):.2f}")

                # Display symbol analysis
                symbol_analysis = analysis.get("symbol_analysis", {})
                if symbol_analysis.get("best_symbols"):
                    print(f"\n🏆 Best Performing Symbols:")
                    for symbol, stats in list(symbol_analysis["best_symbols"].items())[:3]:
                        print(f"   {symbol}: ₹{stats['total_pnl']:,.2f} ({stats['win_rate']:.1f}% win rate, {stats['trades']} trades)")

                if symbol_analysis.get("worst_symbols"):
                    print(f"\n📉 Worst Performing Symbols:")
                    for symbol, stats in list(symbol_analysis["worst_symbols"].items())[:3]:
                        print(f"   {symbol}: ₹{stats['total_pnl']:,.2f} ({stats['win_rate']:.1f}% win rate, {stats['trades']} trades)")

                # Display timing analysis
                timing = analysis.get("timing_analysis", {})
                if timing:
                    print(f"\n⏱️ Timing Analysis:")
                    print(f"   Stop-Loss Hits: {timing.get('stop_loss_hits', 0)}")
                    print(f"   Target Hits: {timing.get('target_hits', 0)}")
                    print(f"   Avg Hold Time: {timing.get('avg_holding_time_minutes', 0):.1f} min")
                    print(f"   {timing.get('insight', '')}")

                # Display patterns
                winning_patterns = analysis.get("winning_patterns", [])
                losing_patterns = analysis.get("losing_patterns", [])

                if winning_patterns:
                    print(f"\n✅ Winning Patterns:")
                    for pattern in winning_patterns:
                        print(f"   • {pattern}")

                if losing_patterns:
                    print(f"\n❌ Losing Patterns:")
                    for pattern in losing_patterns:
                        print(f"   • {pattern}")

                # Display recommendations
                recommendations = analysis.get("recommendations", {}).get("recommendations", [])
                if recommendations:
                    print(f"\n💡 Recommendations ({len(recommendations)}):")
                    for rec in recommendations:
                        priority = rec.get("priority", "MEDIUM")
                        category = rec.get("category", "general")
                        recommendation = rec.get("recommendation", "")
                        reason = rec.get("reason", "")

                        print(f"\n   [{priority}] {recommendation}")
                        print(f"   Category: {category}")
                        print(f"   Reason: {reason}")
                        print(f"   Action: {rec.get('action', 'N/A')}")

                # Apply recommended adjustments
                adjustments = analysis.get("recommendations", {}).get("adjustments", {})
                if adjustments:
                    print(f"\n🔧 Applying Parameter Adjustments:")

                    # Update confidence threshold
                    if "confidence_threshold" in adjustments:
                        old_threshold = self.memory.get("confidence_gate", 0.60)
                        new_threshold = adjustments["confidence_threshold"]
                        self.memory["confidence_gate"] = new_threshold
                        print(f"   Confidence threshold: {old_threshold:.2f} → {new_threshold:.2f}")

                    # Update blacklist
                    if "blacklist_add" in adjustments:
                        for symbol in adjustments["blacklist_add"]:
                            if symbol not in self.memory.get("blacklist", []):
                                self.memory.setdefault("blacklist", []).append(symbol)
                                print(f"   Added to blacklist: {symbol}")

                    self._save_memory()
                    result["adjustments_applied"] = adjustments

            except Exception as e:
                print(f"❌ Error during trade analysis: {e}")
                result["error"] = str(e)

        # 3. Update money manager if needed
        if self.money_manager and analysis and not analysis.get("error"):
            try:
                # Check if circuit breaker should be adjusted based on performance
                win_rate = analysis.get("win_rate", 50)
                if win_rate < 40:
                    print(f"\n⚠️ Low win rate detected - consider reviewing strategy")
                elif win_rate > 70:
                    print(f"\n🎉 Excellent win rate - strategy is working well!")

            except Exception as e:
                print(f"⚠️ Error updating money manager: {e}")

        print(f"\n{'=' * 80}")
        print(f"✅ Learning mode complete!")
        print(f"{'=' * 80}\n")

        self._emit_status("learning_complete", result)
        return result


# -------------------------------
# Convenience wrapper for main.py
# -------------------------------
def run_decision_cycle(symbols: Sequence[str], **kwargs) -> Dict[str, Any]:
    crew = TradingCrew(**kwargs)
    return crew.run_decision_cycle(symbols)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Trading Crew Orchestrator")
    ap.add_argument("--mode", default="live", choices=["live", "backtest"])
    ap.add_argument("--today", default=None)
    ap.add_argument("--live", type=int, default=0)
    ap.add_argument("--wait-open", action="store_true")
    ap.add_argument("--min-confidence", type=float, default=None)
    ap.add_argument("--symbols", nargs="*", default=["ITC", "TCS", "RELIANCE"])
    args = ap.parse_args()

    crew = TradingCrew(
        mode=args.mode,
        today=args.today,
        live=bool(args.live),
        wait_for_open=args.wait_open,
        min_confidence_gate=args.min_confidence,
    )
    summary = crew.run_decision_cycle(args.symbols)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
