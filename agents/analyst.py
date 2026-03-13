"""Analyst node — create_react_agent wrapping GPT-4o-mini.

Short-term trade focus: always produces a full entry/exit plan.
On revisions: uses tools to directly research each auditor counter-signal.
"""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import json
import re
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

import config
import file_memory
import slack_client
from state import OverallState, AnalystThesis
from tools.perplexity import search_web
from tools.market_data import get_pe_ratio, get_price_history, get_technical_indicators


_ANALYST_TOOLS = [search_web, get_pe_ratio, get_price_history, get_technical_indicators]


def analyst_node(state: OverallState) -> dict:
    ticker = state["ticker"]
    report_dir = state["report_dir"]
    revision_count = state.get("revision_count", 0)
    iteration = revision_count + 1

    print(f"\n{'='*60}")
    print(f"[ANALYST] Building thesis for {ticker} (iteration {iteration})")
    print(f"{'='*60}")

    research_report = ""
    if state.get("researcher_report_path"):
        research_report = file_memory.read_report(state["researcher_report_path"])

    # Build revision context — tells analyst exactly what to research
    revision_section = ""
    if revision_count > 0 and state.get("auditor_verdict"):
        verdict = state["auditor_verdict"]
        counter_signals = verdict.get("counter_signals", [])
        feedback = verdict.get("feedback_for_analyst", "")
        critique = verdict.get("critique", "")
        signals_list = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(counter_signals))
        revision_section = f"""
## AUDITOR FEEDBACK — ITERATION {revision_count}
**Critique:** {critique}
**Specific feedback:** {feedback}

**Counter-signals you MUST research before responding:**
{signals_list}

MANDATORY: For each counter-signal above, call search_web or get_technical_indicators
to gather evidence. Then address each one explicitly in your rationale.
Your revised thesis must show you investigated each point — not just acknowledged it.
"""

    system_prompt = f"""You are a short-term equity trader building a trade thesis for {ticker}.

MANDATE: Short-term only — maximum 3-4 week holding period, limit orders only (no market orders).
GOAL: Maximum profit within the horizon, minimum loss if wrong.

## STEP 1 — ALWAYS CALL THESE TOOLS FIRST (required every iteration):
- get_technical_indicators("{ticker}") — current price, MA50, MA200, RSI, volume
- get_price_history("{ticker}", "1mo") — recent price action and key levels
{f'- search_web for EACH counter-signal listed in the auditor feedback below' if revision_count > 0 else '- get_pe_ratio("{ticker}") — valuation context'}

## STEP 2 — RESEARCHER REPORT
{research_report}
{revision_section}

## STEP 3 — BUILD YOUR THESIS
You MUST always provide a complete entry/exit plan regardless of BUY or PASS decision.

**If BUY:**
- entry_limit_price: specific limit order price (at or below current price, ideally at a support level)
- target_price: realistic profit target within 3-4 weeks (identify a resistance level or catalyst target)
- stop_loss: hard stop price (below key support; aim for risk/reward ratio >= 2:1)
- time_horizon: "X-Y weeks" — must be 4 weeks or less
- exit_conditions: at least 3 specific triggers (price levels, events, technical breaks)

**If PASS:**
- entry_limit_price: the price at which you WOULD buy if conditions improve
- target_price: what target you would set if you did buy
- stop_loss: where you would put your stop
- time_horizon: "watching — would re-evaluate in X days"
- exit_conditions: exactly what needs to change for you to flip to BUY
- rationale: be specific about WHY you are passing right now

## RISK/REWARD RULE
Stop loss must give at least 2:1 reward-to-risk:
  (target_price - entry_limit_price) >= 2 * (entry_limit_price - stop_loss)

## OUTPUT FORMAT — respond with ONLY this JSON, no other text:
{{
  "decision": "BUY" or "PASS",
  "entry_limit_price": <float — always required>,
  "target_price": <float — always required>,
  "stop_loss": <float — always required>,
  "time_horizon": "<string — always required, max 4 weeks>",
  "exit_conditions": ["<specific condition 1>", "<specific condition 2>", "<specific condition 3>"],
  "rationale": "<2-3 sentences covering: signal, entry logic, what auditor raised and how you addressed it>"
}}"""

    llm = ChatOpenAI(model=config.ANALYST_MODEL, api_key=config.OPENAI_API_KEY)
    agent = create_react_agent(llm, _ANALYST_TOOLS)

    try:
        result = agent.invoke({"messages": [{"role": "user", "content": system_prompt}]})
    except Exception as exc:
        print(f"[ANALYST] ERROR: OpenAI agent failed: {exc}")
        raise
    final_content = result["messages"][-1].content

    thesis_dict = _parse_json_response(final_content, ticker)
    thesis = AnalystThesis.model_validate(thesis_dict)

    # Persist
    thesis_path = file_memory.write_json(report_dir, f"analyst_v{iteration}.json", thesis.model_dump())
    print(f"[ANALYST] Thesis saved: {thesis_path}")
    print(f"[ANALYST] Decision: {thesis.decision} | Entry: {thesis.entry_limit_price} | Target: {thesis.target_price} | Stop: {thesis.stop_loss}")

    # Slack
    slack_msg = _format_slack_message(ticker, thesis, iteration, report_dir)
    thread_ts = state.get("slack_thread_ts")
    new_ts = slack_client.post_message(slack_msg, thread_ts=thread_ts, token=config.SLACK_ANALYST_BOT_TOKEN)
    if not thread_ts and new_ts:
        thread_ts = new_ts

    return {
        "analyst_thesis": thesis.model_dump(),
        "slack_thread_ts": thread_ts,
    }


def _parse_json_response(content: str, ticker: str) -> dict:
    json_match = re.search(r"\{[\s\S]*\}", content)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass
    print(f"[ANALYST] Warning: could not parse JSON. Using PASS fallback.")
    return {
        "decision": "PASS",
        "entry_limit_price": None,
        "target_price": None,
        "stop_loss": None,
        "time_horizon": "re-evaluate in 5 days",
        "exit_conditions": ["JSON parse failed — manual review required"],
        "rationale": f"Unable to parse structured thesis for {ticker}. Manual review required.",
    }


def _format_slack_message(ticker: str, thesis: AnalystThesis, iteration: int, report_dir: str) -> str:
    signal = "BUY" if thesis.decision == "BUY" else "PASS"
    entry  = f"${thesis.entry_limit_price:.2f}" if thesis.entry_limit_price else "N/A"
    target = f"${thesis.target_price:.2f}"       if thesis.target_price      else "N/A"
    stop   = f"${thesis.stop_loss:.2f}"          if thesis.stop_loss         else "N/A"
    horizon = thesis.time_horizon or "N/A"

    rr = ""
    if thesis.entry_limit_price and thesis.target_price and thesis.stop_loss:
        reward = thesis.target_price - thesis.entry_limit_price
        risk   = thesis.entry_limit_price - thesis.stop_loss
        if risk > 0:
            rr = f"  |  *R/R:* {reward/risk:.1f}x"

    return (
        f"ANALYST -- ${ticker} (Iteration {iteration})\n"
        f"Signal: {signal}  |  Entry: {entry}  |  Target: {target}  |  Stop: {stop}{rr}\n"
        f"Horizon: {horizon}\n"
        f"{thesis.rationale}\n"
        f"Full report: {report_dir}"
    )
