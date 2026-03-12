"""Entry point: python main.py AAPL"""
from __future__ import annotations

import sys
import os
from datetime import datetime
from pathlib import Path

# Load .env before importing anything that reads env vars
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed — env vars must be set manually


def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py <TICKER>")
        print("Example: python main.py AAPL")
        sys.exit(1)

    ticker = sys.argv[1].upper().strip()
    run_id = f"{ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    report_dir = str(Path("reports") / run_id)
    Path(report_dir).mkdir(parents=True, exist_ok=True)

    print(f"\n{'#'*60}")
    print(f"  StockAnalyzer — {ticker}")
    print(f"  Run ID : {run_id}")
    print(f"  Reports: {report_dir}")
    print(f"{'#'*60}\n")

    # Import graph after env is loaded
    from graph import app

    initial_state = {
        "ticker": ticker,
        "run_id": run_id,
        "report_dir": report_dir,
        "researcher_report_path": None,
        "trigger_signals": [],
        "analyst_thesis": None,
        "auditor_verdict": None,
        "revision_count": 0,
        "slack_thread_ts": None,
    }

    final_state = app.invoke(initial_state)

    # ---- Print final result ----
    print(f"\n{'#'*60}")
    print(f"  FINAL RESULT — {ticker}")
    print(f"{'#'*60}")

    thesis = final_state.get("analyst_thesis")
    verdict = final_state.get("auditor_verdict")

    if thesis:
        decision = thesis.get("decision", "UNKNOWN")
        icon = "[BUY]" if decision == "BUY" else "[PASS]"
        print(f"\nSignal  : {icon}")
        if decision == "BUY":
            print(f"Entry   : ${thesis.get('entry_limit_price', 'N/A')}")
            print(f"Target  : ${thesis.get('target_price', 'N/A')}")
            print(f"Stop    : ${thesis.get('stop_loss', 'N/A')}")
            print(f"Horizon : {thesis.get('time_horizon', 'N/A')}")
        print(f"\nRationale: {thesis.get('rationale', '')}")

    if verdict:
        print(f"\nAudit   : {verdict.get('decision', '').upper()}")
        print(f"Critique: {verdict.get('critique', '')}")

    revisions = final_state.get("revision_count", 0)
    print(f"\nRevisions: {revisions}")
    print(f"Reports  : {report_dir}/")
    print()


if __name__ == "__main__":
    main()
