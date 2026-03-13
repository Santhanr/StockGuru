"""Researcher node — deterministic pipeline, no ReAct loop.

Calls all data-collection tools in sequence, then synthesizes a
research report via GPT-4o-mini and writes it to the report directory.
"""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

from langchain_openai import ChatOpenAI

import config
import file_memory
from state import OverallState
from tools.perplexity import search_web
from tools.market_data import get_price_history, get_technical_indicators
from tools.sec_filings import get_recent_form4_filings
from tools.options_flow import get_unusual_options_flow


def researcher_node(state: OverallState) -> dict:
    ticker = state["ticker"]
    report_dir = state["report_dir"]

    print(f"\n{'='*60}")
    print(f"[RESEARCHER] Starting research for {ticker}")
    print(f"{'='*60}")

    # ---- 1. Deterministic data collection ----
    print("[RESEARCHER] Fetching SEC Form 4 insider filings...")
    try:
        form4 = get_recent_form4_filings.invoke({"ticker": ticker})
    except Exception as exc:
        print(f"[RESEARCHER] ERROR: SEC EDGAR failed: {exc}")
        form4 = "SEC EDGAR unavailable."

    print("[RESEARCHER] Fetching unusual options flow...")
    try:
        options = get_unusual_options_flow.invoke({"ticker": ticker})
    except Exception as exc:
        print(f"[RESEARCHER] ERROR: Options flow (yfinance) failed: {exc}")
        options = "Options flow unavailable."

    print("[RESEARCHER] Searching Reddit discussion spike...")
    try:
        reddit = search_web.invoke(
            {"query": f"{ticker} Reddit unusual discussion spike site:reddit.com"}
        )
    except Exception as exc:
        print(f"[RESEARCHER] ERROR: Perplexity search failed: {exc}")
        reddit = "Web search unavailable."

    print("[RESEARCHER] Searching earnings transcript / guidance...")
    try:
        earnings = search_web.invoke(
            {"query": f"{ticker} earnings transcript guidance 2025 2026"}
        )
    except Exception as exc:
        print(f"[RESEARCHER] ERROR: Perplexity search failed: {exc}")
        earnings = "Web search unavailable."

    print("[RESEARCHER] Searching analyst upgrades / news...")
    try:
        analyst_news = search_web.invoke(
            {"query": f"{ticker} latest analyst upgrades downgrades news"}
        )
    except Exception as exc:
        print(f"[RESEARCHER] ERROR: Perplexity search failed: {exc}")
        analyst_news = "Web search unavailable."

    print("[RESEARCHER] Fetching price history & technical indicators...")
    try:
        price_history = get_price_history.invoke({"ticker": ticker, "period": "3mo"})
        technicals = get_technical_indicators.invoke({"ticker": ticker})
    except Exception as exc:
        print(f"[RESEARCHER] ERROR: yfinance market data failed: {exc}")
        price_history = "Price history unavailable."
        technicals = "Technical indicators unavailable."

    # ---- 2. Synthesize with LLM ----
    print("[RESEARCHER] Synthesizing report with LLM...")
    raw_data = f"""
=== TICKER: {ticker} ===

--- SEC EDGAR FORM 4 (INSIDER BUYING) ---
{form4}

--- UNUSUAL OPTIONS FLOW ---
{options}

--- REDDIT / SOCIAL DISCUSSION ---
{reddit}

--- EARNINGS TRANSCRIPT / GUIDANCE ---
{earnings}

--- ANALYST UPGRADES / NEWS ---
{analyst_news}

--- PRICE HISTORY (3mo) ---
{price_history}

--- TECHNICAL INDICATORS ---
{technicals}
"""

    llm = ChatOpenAI(model=config.RESEARCHER_SYNTHESIS_MODEL, api_key=config.OPENAI_API_KEY)
    synthesis_prompt = f"""You are a senior equity research analyst preparing a pre-analysis brief.

Below is raw data gathered about {ticker}. Synthesize it into a structured research report.

Your report MUST include these sections:
1. **Key Trigger Signals** — bullish/bearish signals worth investigating (bullet list, be specific)
2. **Insider Activity** — Form 4 summary: who bought/sold, amounts, dates
3. **Options Flow** — any unusual call/put activity and what it might signal
4. **Social & News Sentiment** — Reddit discussion, analyst actions, recent news
5. **Earnings & Guidance** — key takeaways from recent earnings or guidance
6. **Technical Picture** — price trend, MA positioning, RSI interpretation, volume
7. **Key Risks** — 3-5 risks that a bull thesis must address

Be factual and concise. Flag data gaps (e.g. "no insider activity found").

RAW DATA:
{raw_data}
"""

    try:
        response = llm.invoke(synthesis_prompt)
        report_content = response.content
    except Exception as exc:
        print(f"[RESEARCHER] ERROR: OpenAI synthesis failed: {exc}")
        raise

    # ---- 3. Persist to file ----
    report_path = file_memory.write_report(report_dir, "research_report.md", report_content)
    print(f"[RESEARCHER] Report saved: {report_path}")

    # Extract trigger signals from report (simple heuristic: lines with "Signal" or bullets)
    trigger_signals = [
        line.strip().lstrip("- •*").strip()
        for line in report_content.splitlines()
        if line.strip().startswith(("-", "•", "*")) and len(line.strip()) > 10
    ][:10]

    return {
        "researcher_report_path": report_path,
        "trigger_signals": trigger_signals,
    }
