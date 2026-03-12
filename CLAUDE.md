# CLAUDE.md

## What This Is
A 3-agent LangGraph pipeline that analyzes stocks: **Researcher → Analyst → Auditor**.
Run it: `python main.py AAPL`

## Environment
Python 3.13.7, venv at `./venv/`.
```bash
source venv/Scripts/activate   # Windows Git Bash
```
All API keys go in `.env` (copy from `.env.example`).

## How to Run
```bash
source venv/Scripts/activate
python main.py AAPL
```
Output: BUY/PASS signal printed to console + files written to `./reports/{run_id}/`.

## Architecture

### Pipeline Flow
```
START → [researcher] → [analyst] → [auditor]
                           ↑              |
                           |   revise     ↓
                           +←←  route_after_audit()  →  END (approve or max revisions)
```
Max loop iterations: `MAX_REVISIONS=2` (env var).

### Agent Roles
| Agent | Model | Type | What it does |
|---|---|---|---|
| Researcher | `gpt-4o-mini` | Custom node (no ReAct) | Calls all tools deterministically, synthesizes `research_report.md` |
| Analyst | `gpt-4o-mini` | `create_react_agent` | Reads report, fetches extra data, outputs `AnalystThesis` (BUY/PASS) |
| Auditor | `claude-haiku-4-5-20251001` | `create_react_agent` | Stress-tests thesis, outputs `AuditorVerdict` (approve/revise) |

### State (`state.py`)
`OverallState` TypedDict carries everything between nodes:
- `ticker`, `run_id`, `report_dir`
- `researcher_report_path` — path to `research_report.md`
- `analyst_thesis` — `AnalystThesis.model_dump()` dict (BUY/PASS + prices + rationale)
- `auditor_verdict` — `AuditorVerdict.model_dump()` dict (approve/revise + critique)
- `revision_count` — incremented by auditor each pass
- `slack_thread_ts` — Slack thread ID for replies

### Tools (`tools/`)
| Tool | Source | Key detail |
|---|---|---|
| `search_web` | Perplexity `sonar` API | Requires `PERPLEXITY_API_KEY` |
| `get_pe_ratio`, `get_price_history`, `get_technical_indicators` | yfinance (free) | 50MA, 200MA, RSI(14), volume trend |
| `get_recent_form4_filings` | SEC EDGAR (free, no key) | Must send `User-Agent` header |
| `get_unusual_options_flow` | yfinance options chain | Flags volume > 2x open interest |

### File Layout
```
config.py          — ALL model IDs + env var constants (change one env var to swap any model)
state.py           — OverallState TypedDict, AnalystThesis, AuditorVerdict (Pydantic)
file_memory.py     — write_report / read_report / write_json / read_json helpers
slack_client.py    — post_message(text, thread_ts) — no-ops gracefully if unconfigured
graph.py           — builds + exports compiled `app`
main.py            — entry point, generates run_id, invokes app
tools/             — perplexity.py, market_data.py, sec_filings.py, options_flow.py
agents/            — researcher.py, analyst.py, auditor.py
reports/           — auto-created at runtime, one subfolder per run
```

## Installed Packages
```
langgraph==1.1.2  langchain-core==1.2.18  langchain-openai==1.1.11
langchain-anthropic>=0.3.0  openai==2.26.0  pydantic==2.12.5
requests==2.32.5  yfinance>=0.2.50  slack-sdk>=3.27.0  python-dotenv>=1.0.0
```

## Changing Models
All model IDs default in `config.py` and are overridable via env vars:
```
RESEARCHER_MODEL=gpt-4o-mini   # or gpt-4o
ANALYST_MODEL=gpt-4o-mini      # or gpt-4o
AUDITOR_MODEL=claude-haiku-4-5-20251001  # or claude-sonnet-4-6
PERPLEXITY_MODEL=sonar         # or sonar-pro
```

## Slack (Optional)
Set `SLACK_BOT_TOKEN` + `SLACK_CHANNEL_ID` in `.env`.
Without these, Slack messages print to console instead — pipeline still runs fully.
Analyst opens a thread; auditor replies in the same thread each iteration.

## Common Next Steps
- **Add a new tool**: create in `tools/`, decorate with `@tool`, add to the agent's tool list in its `agents/*.py` file.
- **Add a new agent**: add a node function, register in `graph.py` with `add_node` + `add_edge`.
- **Change loop logic**: edit `route_after_audit()` in `graph.py`.
- **Extend state**: add fields to `OverallState` in `state.py`; initialize them in `main.py`.
