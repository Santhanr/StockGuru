"""Slack bot — listens for natural language investment questions via Socket Mode.

Usage:
    source venv/Scripts/activate
    python slack_bot.py

Understands messages like:
    "Should I invest in Apple?"
    "What do you think about Tesla right now?"
    "Give me a consultation on Microsoft"
    "Is Netflix worth buying this week?"

Responds with a plain YES/NO + buy/sell limit orders.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from openai import OpenAI

import config

# ---- Slack app (bot token for API calls) ----
app = App(token=config.SLACK_BOT_TOKEN)
_openai = OpenAI(api_key=config.OPENAI_API_KEY)


# ---------------------------------------------------------------------------
# Ticker extraction
# ---------------------------------------------------------------------------

def extract_ticker(user_message: str) -> tuple[str, str] | tuple[None, None]:
    """Use GPT-4o-mini to extract company name and ticker from free-form text.
    Returns (ticker, company_name) or (None, None) if no stock found.
    """
    resp = _openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You extract US stock ticker symbols from user messages about investing. "
                    "Reply with JSON only: {\"ticker\": \"AAPL\", \"company\": \"Apple Inc.\"} "
                    "If no stock is mentioned, reply: {\"ticker\": null, \"company\": null}"
                ),
            },
            {"role": "user", "content": user_message},
        ],
        temperature=0,
    )
    import json, re as _re
    raw = resp.choices[0].message.content.strip()
    m = _re.search(r"\{.*\}", raw, _re.DOTALL)
    if not m:
        return None, None
    data = json.loads(m.group())
    ticker = data.get("ticker")
    company = data.get("company")
    if not ticker:
        return None, None
    return ticker.upper(), company


# ---------------------------------------------------------------------------
# Pipeline runner (runs in background thread)
# ---------------------------------------------------------------------------

def run_pipeline(ticker: str, company: str, say, thread_ts: str):
    """Run the full analysis pipeline and post a final summary to Slack."""
    from graph import app as analysis_app

    run_id = f"{ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    report_dir = str(Path("reports") / run_id)
    Path(report_dir).mkdir(parents=True, exist_ok=True)

    # Override slack_client to post into this thread
    import slack_client as sc
    _orig_post = sc.post_message

    def threaded_post(text: str, thread_ts: str | None = None) -> str:
        return _orig_post(text, thread_ts=thread_ts or thread_ts)

    # Patch thread_ts into all pipeline slack posts by pre-seeding state
    initial_state = {
        "ticker": ticker,
        "run_id": run_id,
        "report_dir": report_dir,
        "researcher_report_path": None,
        "trigger_signals": [],
        "analyst_thesis": None,
        "auditor_verdict": None,
        "revision_count": 0,
        "slack_thread_ts": thread_ts,   # all pipeline posts go into this thread
    }

    try:
        final_state = analysis_app.invoke(initial_state)
        _post_final_answer(ticker, company, final_state, say, thread_ts)
    except Exception as exc:
        say(
            text=f"Analysis failed for {ticker}: {exc}",
            thread_ts=thread_ts,
        )


def _post_final_answer(ticker: str, company: str, final_state: dict, say, thread_ts: str):
    """Post the plain-English YES/NO verdict as the final message in the thread."""
    thesis = final_state.get("analyst_thesis") or {}
    verdict = final_state.get("auditor_verdict") or {}
    revisions = final_state.get("revision_count", 0)

    decision = thesis.get("decision", "PASS")
    rationale = thesis.get("rationale", "")
    audit_status = verdict.get("decision", "revise")
    critique = verdict.get("critique", "")

    entry  = thesis.get("entry_limit_price")
    target = thesis.get("target_price")
    stop   = thesis.get("stop_loss")
    horizon = thesis.get("time_horizon", "")

    if decision == "BUY" and audit_status == "approve":
        answer = "YES"
        rr_note = ""
        if entry and target and stop and (entry - stop) > 0:
            rr = (target - entry) / (entry - stop)
            rr_note = f"  |  R/R: {rr:.1f}x"

        body = (
            f"*RECOMMENDATION: YES — Buy {company} ({ticker})*\n\n"
            f"*Buy order (limit):*  ${entry:.2f}\n"
            f"*Sell order (target limit):*  ${target:.2f}\n"
            f"*Stop loss:*  ${stop:.2f}{rr_note}\n"
            f"*Hold for:*  {horizon}\n\n"
            f"_{rationale}_\n\n"
            f"Analyst and auditor reached consensus after {revisions} round(s)."
        )
    elif decision == "BUY" and audit_status == "revise":
        # Analyst wanted to buy but auditor never approved — treat as conditional
        body = (
            f"*RECOMMENDATION: CONDITIONAL — Analyst sees a trade but auditor has reservations*\n\n"
            f"*Analyst proposed:*\n"
            f"  Buy limit: ${entry:.2f}  |  Target: ${target:.2f}  |  Stop: ${stop:.2f}\n"
            f"  Horizon: {horizon}\n\n"
            f"*Auditor concern:* _{critique}_\n\n"
            f"Proceed with caution. Max revisions ({revisions}) reached without full consensus."
        )
    else:
        body = (
            f"*RECOMMENDATION: NO — Do not buy {company} ({ticker}) right now*\n\n"
            f"_{rationale}_\n\n"
            f"*Auditor note:* _{critique}_\n\n"
            f"If conditions change, revisit at:\n"
            f"  Potential entry: ${entry:.2f}  |  Target: ${target:.2f}  |  Stop: ${stop:.2f}"
            if entry else
            f"*RECOMMENDATION: NO — Do not buy {company} ({ticker}) right now*\n\n"
            f"_{rationale}_"
        )

    say(text=body, thread_ts=thread_ts)


# ---------------------------------------------------------------------------
# Slack event handlers
# ---------------------------------------------------------------------------

def _handle_message(message_text: str, say, client, channel: str, ts: str):
    """Shared logic for app_mention and direct messages."""
    # Strip bot mention if present
    clean = re.sub(r"<@[A-Z0-9]+>", "", message_text).strip()
    if not clean:
        say(text="Ask me about any stock — e.g. 'Should I buy Apple?' or 'What do you think about Tesla?'", thread_ts=ts)
        return

    ticker, company = extract_ticker(clean)
    if not ticker:
        say(
            text="I couldn't identify a stock in your message. Try something like: 'Should I invest in Apple?' or 'What about Tesla?'",
            thread_ts=ts,
        )
        return

    # Acknowledge immediately — pipeline takes 2-4 minutes
    say(
        text=(
            f"Got it. Researching *{company} ({ticker})* now.\n"
            f"I'll run a full Researcher → Analyst → Auditor analysis (up to {config.MAX_REVISIONS} rounds) "
            f"and post updates here as I go. This takes 2-4 minutes."
        ),
        thread_ts=ts,
    )

    # Run pipeline in background so Slack doesn't time out
    t = threading.Thread(
        target=run_pipeline,
        args=(ticker, company, say, ts),
        daemon=True,
    )
    t.start()


@app.event("app_mention")
def handle_mention(event, say, client):
    _handle_message(
        message_text=event.get("text", ""),
        say=say,
        client=client,
        channel=event["channel"],
        ts=event["ts"],
    )


@app.event("message")
def handle_dm(event, say, client):
    # Only handle DMs (channel type "im") to avoid double-processing channel messages
    if event.get("channel_type") != "im":
        return
    if event.get("subtype"):   # ignore bot messages, edits, etc.
        return
    _handle_message(
        message_text=event.get("text", ""),
        say=say,
        client=client,
        channel=event["channel"],
        ts=event["ts"],
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not config.SLACK_APP_TOKEN:
        raise SystemExit("SLACK_APP_TOKEN not set in .env — see setup instructions below.")
    print("StockAnalyzer Slack bot starting (Socket Mode)...")
    print(f"Listening for mentions and DMs. Max revisions: {config.MAX_REVISIONS}")
    handler = SocketModeHandler(app, config.SLACK_APP_TOKEN)
    handler.start()
