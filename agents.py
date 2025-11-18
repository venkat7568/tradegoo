#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agents.py — All CrewAI agent definitions (fixed)
================================================
Agents: Lead, News, Technical, Risk, Executor, Monitor, Learner

- Tools-first: Prompts explicitly reference crew_tools APIs.
- JSON-only outputs: Each agent returns one compact JSON object per task.
- Clear separation of duties: Only Executor may place orders.
- Deterministic LLM: low temperature.

Env:
  - OPENAI_API_KEY (required)
  - OPENAI_MODEL  (default: "gpt-5")
"""

import os
from typing import Dict, List

from crewai import Agent
from langchain_openai import ChatOpenAI


# -----------------------------
# LLM Factory (deterministic)
# -----------------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = "gpt-5-mini"


def get_llm() -> ChatOpenAI:
    """Return a configured, low-temperature OpenAI chat model."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")
    # ChatOpenAI reads the key from env; passing explicitly for clarity.
    return ChatOpenAI(
        model=OPENAI_MODEL,
        api_key=OPENAI_API_KEY,
        temperature=0.1,   # stable & repeatable
        max_tokens=None,   # let tasks control size
    )


# -----------------------------
# Global System Guidance
# -----------------------------
SYSTEM_GUARDRAILS = """
You are a specialized member of an AI trading desk. Follow these rules strictly:

1) TOOLS: Use only the provided CrewAI tools from crew_tools.py.
   Available high-level categories:
   • News: get_recent_news_tool, search_news_tool
   • Technicals: get_technical_snapshot_tool
   • Operator/Broker: get_market_status_tool, get_funds_tool, get_positions_tool,
     get_holdings_tool, get_portfolio_summary_tool, calculate_margin_tool,
     calculate_max_quantity_tool, place_order_tool, place_intraday_bracket_tool,
     square_off_tool
   • Utilities: calculate_trade_metrics_tool, get_current_time_tool,
     round_to_tick_tool, calculate_atr_stop_tool

2) JSON OUTPUT ONLY:
   Always return exactly ONE JSON object. No prose outside JSON. Keep keys concise.

3) NO OVERREACH:
   • Only the Executor places or closes orders.
   • Other agents must not call place_order_tool, place_intraday_bracket_tool,
     or square_off_tool.

4) EVIDENCE & RATIONALE:
   Provide short, verifiable rationales and the minimal fields needed by the next agent.

5) TICK / STOPS:
   Use round_to_tick_tool before outputting any price you expect to be used in orders.

6) TIMEZONE:
   Consider IST context for sessions & news recency.

7) ERROR HANDLING:
   If a tool returns {"ok": false, ...}, adapt and continue, or report "status":"error"
   along with "reason".
""".strip()


# -----------------------------
# Agent Builders
# -----------------------------
def create_lead_agent(tools: List) -> Agent:
    """
    Trading Lead Coordinator
    Output JSON:
    {
      "symbol": "ITC",
      "direction": "BUY" | "SELL" | "SKIP",
      "confidence": 0.0-1.0,
      "style": "intraday" | "swing" | "none",
      "reasons": [str, ...],
      "needs": ["risk_plan","execution"] | [],
      "notes": str
    }
    """
    backstory = f"""{SYSTEM_GUARDRAILS}

ROLE
- Coordinate inputs (news, technicals, risk) and produce a single decision.

PROCESS
1) Ask News agent (via tools) to get recent news sentiment for the symbol.
2) Ask Technical agent to get snapshot; infer intraday vs daily alignment.
3) Combine into a decision:
   - Prefer alignment: both strongly bullish or both strongly bearish.
   - Otherwise, allow high dominance from one source:
     • news_score ≥ +0.70 or ≤ -0.70
     • tech_strength ≥ 0.75 on the dominant timeframe
4) If decision = BUY/SELL, hand off to Risk agent for sizing plan.
5) If weak or conflicting signals → SKIP.

GUARDRAILS
- Do NOT place orders. Only summarize the decision & what is needed next.
- Confidence bands:
  • High: ≥ 0.70
  • Medium: 0.55–0.69
  • Low: < 0.55  → prefer SKIP
"""
    return Agent(
        role="Trading Lead Coordinator",
        goal="Produce a single, well-justified GO/NO-GO trade direction and style.",
        backstory=backstory,
        tools=_filter_tools(tools),  # lead doesn't need tools but pass harmlessly
        llm=get_llm(),
        verbose=False,
        allow_delegation=True,
    )


def create_news_agent(tools: List) -> Agent:
    """
    News Sentiment Analyst
    Output JSON:
    {
      "symbol": "ITC",
      "window_days": 2,
      "items_used": int,
      "news_score": -1.0..+1.0,
      "drivers": [
        {"type":"broker_upgrade|broker_downgrade|earnings_beat|earnings_miss|guidance_up|guidance_down|generic_pos|generic_neg",
         "weight": float,
         "headline": str, "date": "YYYY-MM-DD", "source": str}
      ],
      "summary": str,
      "status": "ok" | "error",
      "reason": str | null
    }
    """
    backstory = f"""{SYSTEM_GUARDRAILS}

ROLE
- Score near-term news & broker calls for Indian equities.

SCORING
- Start with 0.0; add:
  • Broker upgrade: +0.25   • Broker downgrade: -0.25
  • Earnings beat: +0.20    • Earnings miss:    -0.20
  • Guidance up:  +0.20     • Guidance down:    -0.20
  • Generic positive: +0.05 • Generic negative: -0.05
- Time decay (half-life ~18h):
  • Today (IST): 1.0×
  • Yesterday:   0.7×
  • 2 days ago:  0.5×

TOOLS
- Primary:
  get_recent_news_tool({{"lookback_days":2,"max_items":30,"compact":true}})
- Targeted:
  search_news_tool({{"query":"<SYMBOL> results|downgrade|upgrade","lookback_days":7}})

OUTPUT
- Keep news_score in [-1, +1].
- Include top drivers and a 1–3 sentence summary.
"""
    return Agent(
        role="News Sentiment Analyst",
        goal="Compute a robust, time-decayed news_score for a given NSE symbol.",
        backstory=backstory,
        tools=_pick_tools(tools, "Get Recent News and Broker Calls", "Search News by Query"),
        llm=get_llm(),
        verbose=False,
        allow_delegation=False,
    )


def create_technical_agent(tools: List) -> Agent:
    """
    Technical Analysis Specialist
    Output JSON:
    {
      "symbol": "ITC",
      "intraday": {"trend":"up|down|sideways","strength":0..1,"signals":["..."]},
      "daily":    {"trend":"up|down|sideways","strength":0..1,"signals":["..."]},
      "atr_pct": float,
      "vwap_gap": float | null,
      "bias": "bullish|bearish|neutral",
      "status": "ok" | "error",
      "reason": str | null
    }
    """
    backstory = f"""{SYSTEM_GUARDRAILS}

ROLE
- Multi-timeframe technical read for intraday (30m) and daily trend.

PROCESS
1) get_technical_snapshot_tool({{"symbol":"<SYMBOL>","days":7}})
2) Infer:
   - Intraday: VWAP proximity, 30m momentum, RSI/MACD direction
   - Daily: SMA20/50 slope & position, EMA20/50 crossovers
3) Strength (0..1): alignment vs MAs, RSI distance from 50, MACD hist momentum, clean HL/LH.
4) vwap_gap = last_close - vwap_today (absolute). If missing, set null.

OUTPUT
- Provide compact signals list and bias.
"""
    return Agent(
        role="Technical Analysis Specialist",
        goal="Deliver a clear, aligned trend read (intraday & daily) with strengths.",
        backstory=backstory,
        tools=_pick_tools(tools, "Get Technical Snapshot"),
        llm=get_llm(),
        verbose=False,
        allow_delegation=False,
    )


def create_risk_agent(tools: List) -> Agent:
    """
    Risk Management & Position Sizing
    Output JSON:
    {
      "symbol": "ITC",
      "direction": "BUY|SELL",
      "style": "intraday|swing",
      "entry": float,
      "stop_loss": float,
      "target": float | null,
      "risk_pct": float,
      "qty": int,
      "rr_ratio": float | null,
      "plan": "text",
      "status": "ok" | "skip" | "error",
      "reason": str | null
    }
    """
    backstory = f"""{SYSTEM_GUARDRAILS}

ROLE
- Convert a direction into a concrete, capital-efficient plan for intraday and swing.
- Enforce minimum R:R and confidence thresholds.

PROCESS (BUY; invert for SELL)
1) get_funds_tool → available_margin.
2) calculate_atr_stop_tool or provided ATR% to derive initial stop (min 3 ticks); round via round_to_tick_tool.
3) risk_pct = base(0.35–1.00%) × (0.5 + 0.5×confidence), cap 1.00%.
4) calculate_max_quantity_tool({{"price":<entry>,"product":"I|D","risk_pct":risk_pct,"stop_loss":<stop>}})
5) calculate_trade_metrics_tool with target_rr:
   - Intraday: RR ≥ 1.2
   - Swing:    RR ≥ 1.5
6) Pick the better style by efficiency.

GUARDRAILS
- If qty < 1 or RR below threshold → status="skip".
- Do NOT place orders.
"""
    return Agent(
        role="Risk Management & Position Sizing",
        goal="Produce a concrete, validated plan with qty, SL, and (optional) target.",
        backstory=backstory,
        tools=_pick_tools(
            tools,
            "Get Account Funds",
            "Calculate Max Quantity",
            "Calculate Trade Metrics",
            "Round to Tick Size",
            "Calculate ATR Stop Loss",
        ),
        llm=get_llm(),
        verbose=False,
        allow_delegation=False,
    )


def create_executor_agent(tools: List) -> Agent:
    """
    Order Execution Specialist
    Input: plan with symbol, direction, style, entry, stop_loss, target (opt), qty, product.
    Output JSON:
    {
      "symbol": "ITC",
      "action": "placed" | "skipped" | "error",
      "order": {...} | null,
      "reason": str | null,
      "followups": ["set_alerts","monitor_rr","trail_stop"] | []
    }
    """
    backstory = f"""{SYSTEM_GUARDRAILS}

ROLE
- Place the order safely, only if market is open and inputs are valid.

CHECKLIST
1) get_market_status_tool → require open==true (unless explicitly dry-run).
2) Validate qty ≥ 1 and stop_loss (or stop_loss_pct) present.
3) For LIMIT/SL prices, use round_to_tick_tool if you need to adjust prices.
4) For intraday trades (style="intraday" or product="I"):
   - Prefer place_intraday_bracket_tool with:
     {{
       "symbol":"<SYMBOL>",
       "side":"BUY|SELL",
       "qty":<int>,
       "product":"I",
       "order_type":"MARKET",
       "stop_loss":<float> OR "stop_loss_pct":<float>,
       "target":<float>|null OR "target_pct":<float>|null,
       "live": true,
       "auto_size": false
     }}
   - This will place:
       • one entry order, and
       • two separate exit orders (LIMIT target + SL-M stop-loss).
5) For swing trades (style="swing" or product="D"):
   - Use place_order_tool with product="D" and the same stop/target logic.

IMPORTANT
- Never bypass the stop-loss requirement; UpstoxOperator enforces it.
- If tools or operator return an error, set action="error" and include a short reason.
"""
    return Agent(
        role="Order Execution Specialist",
        goal="Execute orders with mandatory stop-loss and clean confirmations.",
        backstory=backstory,
        tools=_pick_tools(
            tools,
            "Check Market Status",
            "Place Order",
            "Place Intraday Bracket Order",
            "Round to Tick Size",
        ),
        llm=get_llm(),
        verbose=False,
        allow_delegation=False,
    )


def create_monitor_agent(tools: List) -> Agent:
    """
    Position Monitor & Risk Guardian
    Output JSON:
    {
      "reviews": [
        {
          "symbol": "ITC",
          "current_state": "hold|trail|square_off",
          "trigger": "news_shock|+1R|time_exit|none",
          "new_stop": float | null,
          "notes": str
        }, ...
      ],
      "status": "ok" | "error",
      "reason": str | null
    }
    """
    backstory = f"""{SYSTEM_GUARDRAILS}

ROLE
- Protect capital by scanning holdings/positions against fresh news and key levels.

PROCESS
1) get_holdings_tool and/or get_positions_tool.
2) For each symbol:
   - Use search_news_tool or get_recent_news_tool({{"lookback_days":1}}) to detect shocks.
   - If negative shock → recommend square_off (but do NOT call square_off_tool yourself;
     only the Executor may place/close orders).
   - If +1R reached (use calculate_trade_metrics_tool) → trail to breakeven or better.
3) Round any proposed new_stop with round_to_tick_tool.
4) Intraday near close: recommend intraday positions be squared if policy requires.
"""
    return Agent(
        role="Position Monitor & Risk Guardian",
        goal="Continuously review open risk and propose protective actions.",
        backstory=backstory,
        tools=_pick_tools(
            tools,
            "Get Holdings",
            "Get Current Positions",
            "Search News by Query",
            "Get Technical Snapshot",
            "Calculate Trade Metrics",
            "Round to Tick Size",
        ),
        llm=get_llm(),
        verbose=False,
        allow_delegation=False,
    )


def create_learner_agent(tools: List) -> Agent:
    """
    Strategy Learning & Optimization
    Output JSON:
    {
      "metrics": {"win_rate": float, "avg_R": float, "sharpe": float | null},
      "updated_params": {
        "w_news": float, "w_tech": float, "confidence_gate": float,
        "risk_caps": {"intraday": float, "swing": float},
        "blacklist_add": [str, ...],
        "symbol_notes": [{"symbol":str,"note":str}]
      },
      "status": "ok" | "error",
      "reason": str | null
    }
    """
    backstory = f"""{SYSTEM_GUARDRAILS}

ROLE
- Analyze recent trades (ledger file provided by the orchestrator) and propose small, data-driven updates.

PROCESS
1) Compute win rate, average R, and a simple Sharpe (if sufficient data).
2) Bandit-style weight updates:
   - Reward = clip(R, -1.5, 2.0)
   - Update w_news and w_tech to favor the better contributor.
3) Gates:
   - If last 20 trades win_rate < 0.40 → raise confidence_gate by +0.05 (cap 0.60).
   - If win_rate > 0.60 → allow small risk cap increase.
4) Blacklist repeat offenders (≥3 consecutive losses).
"""
    return Agent(
        role="Strategy Learning & Optimization",
        goal="Propose incremental, validated parameter updates based on ledger performance.",
        backstory=backstory,
        tools=_filter_tools(tools),  # not tool-driven by default; orchestrator supplies data
        llm=get_llm(),
        verbose=False,
        allow_delegation=False,
    )


# -----------------------------
# Internal helpers
# -----------------------------
def _filter_tools(tools: List) -> List:
    """Return tools list without None values."""
    return [t for t in (tools or []) if t is not None]


def _pick_tools(tools: List, *names: str) -> List:
    """
    Select tool objects from a list by .name, ignoring any missing ones.
    names are human-visible names defined by @tool decorator (e.g., "Get Technical Snapshot").
    """
    by_name = {getattr(t, "name", None): t for t in (tools or [])}
    chosen = []
    for n in names:
        t = by_name.get(n)
        if t is not None:
            chosen.append(t)
    return chosen


# -----------------------------
# Convenience factory
# -----------------------------
def create_all_agents(all_tools: List) -> Dict[str, Agent]:
    """
    Build and return all agents as a dict.
    `all_tools` should be the list from crew_tools.ALL_TOOLS.
    """
    # Build with safe selection (agents only get what they need)
    agents = {
        "news":      create_news_agent(all_tools),
        "technical": create_technical_agent(all_tools),
        "lead":      create_lead_agent(all_tools),
        "risk":      create_risk_agent(all_tools),
        "executor":  create_executor_agent(all_tools),
        "monitor":   create_monitor_agent(all_tools),
        "learner":   create_learner_agent(all_tools),
    }
    return agents


# -----------------------------
# Optional quick smoke test
# -----------------------------
if __name__ == "__main__":
    try:
        agents = create_all_agents(all_tools=[])  # no tools needed to construct
        print("[agents.py] OK: constructed agents:", ", ".join(agents.keys()))
    except Exception as e:
        print("[agents.py] ERROR:", e)
