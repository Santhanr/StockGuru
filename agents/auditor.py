"""Auditor node — create_react_agent wrapping Claude Haiku (or GPT fallback).

Reviews analyst entry/exit plan for short-term realism.
Checks: entry level, target achievability, stop loss R/R, near-term risks.
"""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import json
import re
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

import config
import file_memory
import slack_client
from state import OverallState, AuditorVerdict, AnalystThesis
from tools.perplexity import search_web
from tools.market_data import get_price_history, get_technical_indicators


_AUDITOR_TOOLS = [search_web, get_price_history, get_technical_indicators]


def auditor_node(state: OverallState) -> dict:
    ticker = state["ticker"]
    report_dir = state["report_dir"]
    revision_count = state.get("revision_count", 0)
    iteration = revision_count + 1

    print(f"\n{'='*60}")
    print(f"[AUDITOR] Reviewing thesis for {ticker} (iteration {iteration})")
    print(f"{'='*60}")

    research_report = ""
    if state.get("researcher_report_path"):
        research_report = file_memory.read_report(state["researcher_report_path"])

    thesis_dict = state.get("analyst_thesis", {})
    thesis_json = json.dumps(thesis_dict, indent=2) if thesis_dict else "No thesis provided."

    # Load prior auditor verdicts for context
    prior_verdicts = ""
    if revision_count > 0:
        prior_rounds = []
        for i in range(1, revision_count + 1):
            try:
                v = file_memory.read_json(f"{report_dir}/auditor_v{i}.json")
                prior_rounds.append(f"Round {i}: {v.get('decision','?').upper()} — {v.get('critique','')}")
            except Exception:
                pass
        if prior_rounds:
            prior_verdicts = "\n## PRIOR AUDIT ROUNDS\n" + "\n".join(prior_rounds) + "\n"

    system_prompt = f"""You are a senior risk manager reviewing a short-term trade thesis for {ticker}.
This is audit iteration {iteration} of {config.MAX_REVISIONS}.
{prior_verdicts}
## ANALYST THESIS TO REVIEW
{thesis_json}

## RESEARCHER REPORT (context)
{research_report[:2500]}

## YOUR REVIEW — work through each step using your tools:

### STEP 1 — PRICE LEVEL VALIDATION (call get_technical_indicators and get_price_history first)
- Is the entry_limit_price at a sensible level? (near support, not chasing)
- Is target_price realistic within the stated time horizon? (check recent price velocity)
- Is stop_loss below a real support level, or arbitrary?
- Calculate R/R: (target - entry) / (entry - stop). Flag if < 2.0

### STEP 2 — NEAR-TERM RISK SCAN (call search_web)
Search for: "{ticker} earnings date upcoming"
Search for: "{ticker} macro risk next 4 weeks catalyst"
- Are there earnings, Fed meetings, or sector events within the trade horizon that create binary risk?
- Any regulatory, geopolitical, or company-specific news that invalidates the thesis?

### STEP 3 — THESIS LOGIC CHECK
- Does the BUY/PASS decision logically follow from the data?
- Are the exit conditions specific and actionable, or vague?
- If this is a revision: did the analyst actually address your prior feedback with new research?

### STEP 4 — YOUR VERDICT
APPROVE if: entry/target/stop levels are sound, R/R >= 2:1, no binary risk events, thesis is internally consistent.
REVISE if: any of the above fail — be specific about exactly what prices/logic to fix.

On the final iteration ({config.MAX_REVISIONS}), lean toward APPROVE if the thesis is directionally sound
even if not perfect — a consensus must be reached.

## OUTPUT FORMAT — respond with ONLY this JSON, no other text:
{{
  "decision": "approve" or "revise",
  "critique": "<2-3 sentences: what you checked, what you found, what you decided>",
  "counter_signals": [
    "<specific concern 1 with data — e.g. 'RSI 37 is oversold but earnings on Mar 28 create binary risk'>",
    "<specific concern 2>",
    "<specific concern 3 if any>"
  ],
  "feedback_for_analyst": "<if revising: exact instructions — specific prices to adjust, specific searches to run, specific logic to fix. If approving: null>"
}}"""

    if config.AUDITOR_MODEL.startswith("gpt"):
        llm = ChatOpenAI(model=config.AUDITOR_MODEL, api_key=config.OPENAI_API_KEY)
    else:
        llm = ChatAnthropic(model=config.AUDITOR_MODEL, api_key=config.ANTHROPIC_API_KEY)

    agent = create_react_agent(llm, _AUDITOR_TOOLS)
    result = agent.invoke({"messages": [{"role": "user", "content": system_prompt}]})
    final_content = result["messages"][-1].content

    verdict_dict = _parse_json_response(final_content, ticker)
    verdict = AuditorVerdict.model_validate(verdict_dict)

    # Persist
    verdict_path = file_memory.write_json(report_dir, f"auditor_v{iteration}.json", verdict.model_dump())
    print(f"[AUDITOR] Verdict saved: {verdict_path}")
    print(f"[AUDITOR] Decision: {verdict.decision.upper()}")
    if verdict.feedback_for_analyst:
        print(f"[AUDITOR] Feedback: {verdict.feedback_for_analyst[:200]}")

    # Slack
    slack_msg = _format_slack_message(ticker, verdict, iteration)
    slack_client.post_message(slack_msg, thread_ts=state.get("slack_thread_ts"))

    return {
        "auditor_verdict": verdict.model_dump(),
        "revision_count": revision_count + 1,
    }


def _parse_json_response(content: str, ticker: str) -> dict:
    json_match = re.search(r"\{[\s\S]*\}", content)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass
    print(f"[AUDITOR] Warning: could not parse JSON. Using approve fallback.")
    return {
        "decision": "approve",
        "critique": f"Audit parsing failed for {ticker}. Defaulting to approve — manual review recommended.",
        "counter_signals": [],
        "feedback_for_analyst": None,
    }


def _format_slack_message(ticker: str, verdict: AuditorVerdict, iteration: int) -> str:
    decision_label = "APPROVED" if verdict.decision == "approve" else "REVISION REQUESTED"
    counter = "\n".join(f"  - {s}" for s in verdict.counter_signals) if verdict.counter_signals else "  - None"
    feedback = f"\nFeedback: {verdict.feedback_for_analyst}" if verdict.feedback_for_analyst else ""

    return (
        f"AUDITOR -- ${ticker} (Iteration {iteration})\n"
        f"Decision: {decision_label}\n"
        f"{verdict.critique}\n"
        f"Counter-signals:\n{counter}"
        f"{feedback}"
    )
