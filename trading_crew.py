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
    square_off_tool,
    calculate_trade_metrics_tool,
    get_current_time_tool,
    round_to_tick_tool,
    calculate_atr_stop_tool,
)

# Direct clients (optional/fallbacks)
from upstox_technical import UpstoxTechnicalClient
from news_client import NewsClient

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
    ):
        self.mode = mode
        self.today = today or datetime.now(IST).strftime("%Y-%m-%d")
        self.live = bool(live)
        self.wait_for_open = bool(wait_for_open)
        self.min_confidence_gate = min_confidence_gate

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
            print("✅ Operator initialized" if self.operator else "⚠️ Operator not available")
        except Exception as e:
            print(f"⚠️ Operator init failed: {e}")
            self.operator = None

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

        try:
            while True:
                status = self.operator.market_session_status()
                is_open = status.get("open", False)
                phase = status.get("status", "UNKNOWN")
                self._emit_status("market_status", {"open": is_open, "phase": phase})
                if is_open:
                    print(f"✅ Market is open (phase: {phase})")
                    break
                print(f"⏳ Market closed (phase: {phase}), waiting...")
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
    # Decision (news + tech)
    # -------------------------------
    def decide_trade(self, symbol: str) -> Dict[str, Any]:
        self._emit_status("decide_start", {"symbol": symbol})

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
        decision_desc = _tmpl(
            """Make a trading decision for $symbol by synthesizing News + Technicals.

Memory:
$memory

Rules:
1) If both align → use that direction.
2) If conflict → require dominance (news ≥ 0.7 OR tech ≥ 0.75).
3) confidence = w_tech*tech_strength*dir_sign + w_news*news_score, clamp [0,1].
4) Gate: confidence >= $gate else SKIP.

Return JSON only:
{"direction":"BUY|SELL|SKIP","confidence":0..1,"rationale":"..."}
""",
            symbol=symbol,
            memory=mem_view,
            gate=self.memory.get("confidence_gate", 0.50),
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
            verbose=False,
        )

        self._emit_status("decision_analyzing", {"symbol": symbol})
        result = str(crew.kickoff()).strip()

        try:
            parsed = json.loads(result) if result.startswith("{") else {}
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
            self._log_incident(
                {
                    "type": "decision_parse_error",
                    "symbol": symbol,
                    "error": str(e),
                    "raw_result": result,
                }
            )
            decision = {
                "symbol": symbol,
                "direction": "SKIP",
                "reason": "parse_error",
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

        # 2) Ask Risk agent for a plan
        risk_desc = _tmpl(
            """Build position plan for $symbol.

Inputs:
- Direction: $direction
- Confidence: $confidence
- Technical Snapshot: $snapshot

Steps:
1) Derive ATR-based SL: intraday ≈ 0.8–1.2 × ATR%, swing ≈ 1.2–2.0 × ATR%, min 3 ticks (0.15).
2) Get funds → compute feasible qty with margin model.
3) Propose BOTH plans:
   - Intraday: product="I", RR≥1.2
   - Swing:    product="D", RR≥1.5
4) Pick higher efficiency = confidence × (exp_profit/capital)/time_days.
5) Return JSON plan (you may include both, but clearly mark the final choice):
{
  "style":"intraday|swing",
  "product":"I|D",
  "direction":"BUY|SELL",
  "side":"BUY|SELL",
  "qty":<int>,
  "entry":<float>,
  "stop_loss":<float>|null,
  "stop_loss_pct":<float>|null,
  "target":<float>|null,
  "target_pct":<float>|null,
  "order_type":"MARKET|LIMIT",
  "rationale":"..."
}

If not feasible → {"decision":"SKIP","reason":"..."}.
Always return exactly ONE JSON object, no text outside JSON.
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
            verbose=False,
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

        # Unwrap possible nested plan structures: {"final_choice": {...}} / {"plan": {...}} etc.
        chosen = None
        for key in ("final_choice", "plan", "intraday", "swing"):
            v = plan_obj.get(key)
            if isinstance(v, dict):
                chosen = v
                break
        if chosen is None:
            chosen = plan_obj  # assume flat

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

        # Clean plan JSON that we pass to executor
        cleaned_plan_str = json.dumps(chosen, ensure_ascii=False)

        # 4) Executor agent: place order using cleaned plan
        exec_desc = _tmpl(
            """Execute trade for $symbol using place_order_tool.

IMPORTANT:
- Use "order_type".
- Pass "live": $live.
- product must be "I" or "D".
- Stop-loss is MANDATORY: provide stop_loss OR stop_loss_pct in the payload.
- target/target_pct are OPTIONAL.

Input (Position Plan JSON from previous step):
$plan

DO:
1) If Mode="$mode" and live=$live, verify market via get_market_status_tool.
2) Build payload with keys:
   symbol, side, qty, product, order_type, price,
   stop_loss, stop_loss_pct, target, target_pct, live, tag.
3) Call place_order_tool and return its JSON result.

Return only JSON like:
{"status":"ok|error","order":{...},"notes":"..."}
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
            verbose=False,
        )

        exec_result = str(exec_crew.kickoff()).strip()

        result = {
            "symbol": symbol,
            "direction": direction,
            "plan": cleaned_plan_str,
            "execution": exec_result,
            "timestamp": datetime.now(IST).isoformat(),
        }
        self._emit_status("execution_complete", result)
        self._append_ledger(result)
        return result

    # -------------------------------
    # Full cycle
    # -------------------------------
    def run_decision_cycle(self, symbols: Sequence[str]) -> Dict[str, Any]:
        self._emit_status(
            "cycle_start",
            {"symbols": list(symbols), "mode": self.mode, "date": self.today},
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
        }

        holdings_actions = self.review_holdings()
        cycle_results["holdings_review"] = holdings_actions

        for symbol in symbols:
            try:
                print(f"\n{'=' * 80}\n🎯 Processing {symbol}\n{'=' * 80}")
                decision = self.decide_trade(symbol)
                cycle_results["decisions"].append(decision)

                if decision.get("direction") in ("BUY", "SELL"):
                    confidence = float(
                        decision.get("confidence")
                        or self.memory.get("confidence_gate", 0.50)
                    )
                    execution = self.size_and_execute(
                        symbol, decision["direction"], confidence
                    )
                    cycle_results["executions"].append(execution)

                time.sleep(0.4)

            except Exception as e:
                print(f"❌ Error processing {symbol}: {e}")
                self._log_incident(
                    {
                        "type": "symbol_processing_error",
                        "symbol": symbol,
                        "error": str(e),
                    }
                )
                cycle_results["decisions"].append(
                    {
                        "symbol": symbol,
                        "direction": "ERROR",
                        "error": str(e),
                    }
                )

        cycle_results["end_time"] = datetime.now(IST).isoformat()
        self._save_decisions(cycle_results)
        self._emit_status("cycle_complete", cycle_results)
        return cycle_results

    # -------------------------------
    # Learning mode
    # -------------------------------
    def run_learning_mode(self) -> Dict[str, Any]:
        self._emit_status("learning_start", {})

        desc = _tmpl(
            """Analyze ledger and output small parameter tweaks.

Current Memory:
$memory

Return JSON with:
- w_news (0..1), w_tech (0..1), confidence_gate (0..1), risk_base_pct (0..2),
- blacklist_delta: {"add":[...],"remove":[...]}""",
            memory=self.memory,
        )

        task = Task(
            description=desc,
            agent=self.agents["learner"],
            expected_output="JSON",
        )

        crew = Crew(
            agents=[self.agents["learner"]],
            tasks=[task],
            process=Process.sequential,
            verbose=False,
        )

        result = str(crew.kickoff()).strip()

        try:
            upd = json.loads(result) if result.startswith("{") else {}
            for k in ("w_news", "w_tech", "confidence_gate", "risk_base_pct"):
                if k in upd:
                    self.memory[k] = float(upd[k])
            if "blacklist_delta" in upd:
                bl = set(self.memory.get("blacklist", []))
                add = set(upd["blacklist_delta"].get("add", []))
                rem = set(upd["blacklist_delta"].get("remove", []))
                self.memory["blacklist"] = sorted(list((bl | add) - rem))
            self._save_memory()
            self._emit_status("learning_complete", {"updated": self.memory})
            return {"status": "complete", "updated": self.memory}
        except Exception as e:
            self._log_incident(
                {"type": "learning_error", "error": str(e), "raw": result}
            )
            return {"status": "error", "error": str(e), "raw": result}


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
