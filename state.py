from typing import Literal, Optional
from typing_extensions import TypedDict
from pydantic import BaseModel


class AnalystThesis(BaseModel):
    decision: Literal["BUY", "PASS"]
    entry_limit_price: Optional[float] = None   # None if PASS
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    time_horizon: Optional[str] = None          # e.g. "4-6 weeks"
    exit_conditions: list[str] = []
    rationale: str                               # 2-3 sentences for Slack


class AuditorVerdict(BaseModel):
    decision: Literal["approve", "revise"]
    critique: str                                # 2-3 sentences for Slack
    counter_signals: list[str] = []
    feedback_for_analyst: Optional[str] = None  # only if decision == "revise"


class OverallState(TypedDict):
    ticker: str
    run_id: str                          # e.g. "AAPL_20260312_143022"
    report_dir: str                      # ./reports/{run_id}/
    researcher_report_path: Optional[str]
    trigger_signals: list[str]
    analyst_thesis: Optional[dict]       # .model_dump() — reconstruct with AnalystThesis.model_validate()
    auditor_verdict: Optional[dict]
    revision_count: int                  # incremented by auditor; max = MAX_REVISIONS
    slack_thread_ts: Optional[str]       # set by analyst's first Slack post
