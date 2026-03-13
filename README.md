# StockGuru — Multi-Agent Stock Analysis System

StockGuru is a stock analysis system built on a 3-agent LangGraph pipeline. Users ask a question in Slack ("Should I buy AAPL?") and receive a structured BUY or PASS recommendation, complete with entry price, target, stop-loss, and risk/reward ratio. Behind the scenes, three AI agents — a Researcher, an Analyst, and a SeniorAnalyst — collaborate and debate until they reach consensus.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Deployment & Infrastructure](#2-deployment--infrastructure)
3. [How Slack Messages Reach the Pipeline](#3-how-slack-messages-reach-the-pipeline)
4. [The LangGraph Pipeline](#4-the-langgraph-pipeline)
5. [How Nodes Share Data — Contracts](#5-how-nodes-share-data--contracts)
6. [Agents](#6-agents)
7. [Tools](#7-tools)
8. [Slack Bots](#8-slack-bots)
9. [File Layout](#9-file-layout)
10. [Configuration & Environment Variables](#10-configuration--environment-variables)
11. [Running Locally](#11-running-locally)
12. [Common Modifications](#12-common-modifications)
13. [FAQ](#13-faq)

---

## 1. System Overview

```
User (Slack)
    │
    │  "@StockGuru Should I buy AAPL?"
    ▼
slack_bot.py  (Railway container, always on)
    │  extracts ticker via GPT-4o-mini
    │  spawns background thread
    ▼
┌─────────────────────────────────────────────────────────────┐
│                    LangGraph Pipeline                       │
│                                                             │
│   START                                                     │
│     │                                                       │
│     ▼                                                       │
│   [Researcher]  — deterministic tool calls                  │
│     │             writes research_report.md                 │
│     ▼                                                       │
│   [Analyst]     — ReAct agent, builds AnalystThesis         │
│     │                              ▲                        │
│     ▼                              │ revise                 │
│   [SeniorAnalyst] — ReAct agent, produces AuditorVerdict    │
│     │                                                       │
│     ├── approve ──────────────────────────────► END         │
│     └── revise (revision_count < MAX_REVISIONS) ──► Analyst │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
Slack Thread
  @StockGuru      — initial acknowledgement + final YES/NO verdict + report link
  @Analyst        — posts thesis each iteration (entry / target / stop / R:R)
  @SeniorAnalyst  — posts audit verdict each iteration (approve / revise + critique)
```

Each analysis run takes 2–4 minutes. All intermediate messages appear in a single Slack thread so the conversation between Analyst and SeniorAnalyst is readable.

---

## 2. Deployment & Infrastructure

| Component | Where it runs | Notes |
|---|---|---|
| `slack_bot.py` | Railway (Docker container) | Always-on process, no web server |
| LangGraph pipeline | Same Railway container | Runs in a background thread per request |
| Research reports | dpaste.com | Uploaded after each run; link posted to Slack |
| API keys | Railway Variables tab | Encrypted at rest, injected as env vars at runtime |
| Local development | Your machine | Use `python main.py AAPL` for CLI runs without Slack |

The Dockerfile (`python:3.13-slim`) installs dependencies and runs `slack_bot.py` as the container's main process. `railway.toml` sets `restartPolicyType = "always"` so Railway automatically restarts the container if it crashes. Every push to the `main` branch triggers an automatic Railway redeploy.

### Diagram: Container startup and Slack connection

```
GitHub (main branch)
    │
    │  git push
    ▼
Railway
    │  detects new commit
    │  builds Docker image (Dockerfile)
    │  installs requirements.txt
    │  injects env vars from Railway Variables
    │
    │  CMD ["python", "slack_bot.py"]
    ▼
┌──────────────────────────────────────────────────┐
│              Railway Container                   │
│                                                  │
│  slack_bot.py starts                             │
│    │                                             │
│    │  App(token=SLACK_BOT_TOKEN)                 │
│    │  SocketModeHandler(app, SLACK_APP_TOKEN)    │
│    │  handler.start()  ◄── blocking call         │
│    │                                             │
│    │  opens outbound WebSocket to Slack          │
│    └─────────────────────────────────────────►  │──────────────────────┐
│                                                  │                      │
│  Container is now idle, holding the connection   │                      ▼
│  No HTTP server. No port exposed.                │           Slack's servers
│  Railway sees a running process → stays up       │           (WebSocket endpoint)
└──────────────────────────────────────────────────┘                      │
         ▲                                                                 │
         │  Slack pushes events down the WebSocket                         │
         │  whenever a user messages the bot                               │
         └─────────────────────────────────────────────────────────────────┘
```

---

## 3. How Slack Messages Reach the Pipeline

There is **no web server** in the container. The system uses **Slack's Socket Mode** — a persistent WebSocket connection that the container opens *outbound* to Slack on startup. Slack then pushes events (user messages) down that connection. Railway doesn't need to expose any port or public URL.

### Diagram: Message arrives → pipeline starts

```
User types in Slack
  "@StockGuru should I buy AAPL?"
    │
    │  (mention in a channel, or DM)
    ▼
Slack servers
    │  wraps the message as an event payload
    │  pushes it down the open WebSocket
    ▼
slack_bot.py  (inside Railway container)
    │
    │  slack_bolt matches event type:
    │    @app.event("app_mention")  ← channel mention
    │    @app.event("message")      ← DM (channel_type == "im")
    │
    ▼
_handle_message(message_text, say, client, channel, ts)
    │
    │  strips "@StockGuru" from message text
    │  calls extract_ticker(clean_text)
    │    └─► POST to OpenAI GPT-4o-mini
    │        prompt: "extract ticker symbol as JSON"
    │        returns: { "ticker": "AAPL", "company": "Apple Inc." }
    │
    │  calls say(...)  ← posts via StockGuru bot token
    │    "Got it. Researching AAPL now. This takes 2–4 minutes."
    │    (Slack thread is opened here; thread_ts recorded)
    │
    │  spawns background thread (daemon=True)
    │    so Slack doesn't time out waiting for a response
    ▼
run_pipeline(ticker, company, say, thread_ts)   [background thread]
    │
    │  builds initial_state dict (ticker, run_id, report_dir, ...)
    │  calls app.invoke(initial_state)  ← LangGraph takes over
    │
    ▼
  [LangGraph pipeline runs — see Section 4]
    │
    ▼
_post_final_answer(...)   posts YES/NO verdict as StockGuru
_share_research_report()  uploads report to dpaste.com, posts link
```

**Entry point**: `Dockerfile` runs `CMD ["python", "slack_bot.py"]`, which calls `SocketModeHandler(app, SLACK_APP_TOKEN).start()`. This is a blocking call that keeps the process alive indefinitely.

---

## 4. The LangGraph Pipeline

The pipeline is a **state machine** defined in `graph.py` using the LangGraph library. Each node is a plain Python function. LangGraph handles execution order, passes state between nodes, and evaluates routing decisions after each node runs.

### How it maps to a state machine

| State machine concept | LangGraph equivalent |
|---|---|
| Shared state | `OverallState` TypedDict — one dict passed to every node |
| State transition | A node function — reads state, returns a dict of updates |
| Fixed edge | `add_edge("researcher", "analyst")` — always goes here next |
| Conditional edge | `add_conditional_edges("auditor", route_after_audit)` — function decides |
| Machine execution | `app.invoke(initial_state)` — LangGraph drives the whole run |

### Graph definition (`graph.py`)

```python
from langgraph.graph import StateGraph, START, END

workflow = StateGraph(OverallState)
workflow.add_node("researcher",    researcher_node)
workflow.add_node("analyst",       analyst_node)
workflow.add_node("auditor",       auditor_node)

workflow.add_edge(START,           "researcher")   # always start here
workflow.add_edge("researcher",    "analyst")      # researcher always feeds analyst
workflow.add_edge("analyst",       "auditor")      # analyst always feeds auditor
workflow.add_conditional_edges("auditor", route_after_audit)  # dynamic

app = workflow.compile()  # produces the runnable invoked by slack_bot.py
```

### Routing logic

`route_after_audit(state)` is called after every auditor run:
- Returns `END` if `auditor_verdict.decision == "approve"`
- Returns `END` if `revision_count >= MAX_REVISIONS` (forces consensus)
- Returns `"analyst"` otherwise — loops back for another round

### Diagram: Pipeline execution, data flow, and Slack posting

```
slack_bot.py
    │
    │  app.invoke(initial_state)
    │  initial_state = {
    │    ticker, run_id, report_dir,
    │    researcher_report_path: None,
    │    analyst_thesis: None,
    │    auditor_verdict: None,
    │    revision_count: 0,
    │    slack_thread_ts: <ts from StockGuru's ack message>
    │  }
    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  LangGraph StateGraph                                                   │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  RESEARCHER NODE  (agents/researcher.py)                         │   │
│  │                                                                  │   │
│  │  Reads from state:  ticker, report_dir                           │   │
│  │                                                                  │   │
│  │  Calls tools in fixed sequence:                                  │   │
│  │    get_recent_form4_filings(ticker)   → SEC EDGAR API            │   │
│  │    get_unusual_options_flow(ticker)   → yfinance options chain   │   │
│  │    search_web("reddit discussion...") → Perplexity API           │   │
│  │    search_web("earnings transcript...") → Perplexity API         │   │
│  │    search_web("analyst upgrades...")  → Perplexity API           │   │
│  │    get_price_history(ticker, "3mo")   → yfinance                 │   │
│  │    get_technical_indicators(ticker)   → yfinance                 │   │
│  │                                                                  │   │
│  │  Calls GPT-4o-mini to synthesize raw data into report            │   │
│  │  Writes: reports/{run_id}/research_report.md                     │   │
│  │                                                                  │   │
│  │  Returns to state:                                               │   │
│  │    researcher_report_path = "reports/.../research_report.md"     │   │
│  │    trigger_signals = ["bullet 1", "bullet 2", ...]               │   │
│  └──────────────────────────┬───────────────────────────────────────┘   │
│                             │ LangGraph merges return dict into state   │
│                             ▼                                           │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  ANALYST NODE  (agents/analyst.py)                  [iteration N]│   │
│  │                                                                  │   │
│  │  Reads from state:  ticker, researcher_report_path,              │   │
│  │                     auditor_verdict (if revision), slack_thread_ts│  │
│  │                                                                  │   │
│  │  Reads file:  file_memory.read_report(researcher_report_path)    │   │
│  │                                                                  │   │
│  │  ReAct loop (LangGraph create_react_agent):                      │   │
│  │    get_technical_indicators → get_price_history                  │   │
│  │    get_pe_ratio  (iteration 1 only)                              │   │
│  │    search_web per auditor counter-signal (revisions only)        │   │
│  │    ... reasons until it produces final JSON thesis ...           │   │
│  │                                                                  │   │
│  │  Validates JSON → AnalystThesis Pydantic model                   │   │
│  │  Writes: reports/{run_id}/analyst_v{N}.json                      │   │
│  │                                                                  │   │
│  │  Posts to Slack as @Analyst (SLACK_ANALYST_BOT_TOKEN):           │   │──► Slack thread
│  │    "ANALYST — $AAPL (Iteration N)                                │   │    @Analyst posts
│  │     Signal: BUY | Entry: $210 | Target: $225 | Stop: $203 | R/R: 2.1x"
│  │                                                                  │   │
│  │  Returns to state:                                               │   │
│  │    analyst_thesis = { decision, entry, target, stop, ... }       │   │
│  │    slack_thread_ts = <thread ts>                                 │   │
│  └──────────────────────────┬───────────────────────────────────────┘   │
│                             │                                           │
│                             ▼                                           │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  SENIORANALYST NODE  (agents/auditor.py)            [iteration N]│   │
│  │                                                                  │   │
│  │  Reads from state:  ticker, analyst_thesis,                      │   │
│  │                     researcher_report_path, revision_count,      │   │
│  │                     slack_thread_ts                              │   │
│  │                                                                  │   │
│  │  Reads file:  file_memory.read_report(researcher_report_path)    │   │
│  │  Reads file:  auditor_v{prev}.json  (for prior round context)    │   │
│  │                                                                  │   │
│  │  ReAct loop (LangGraph create_react_agent):                      │   │
│  │    get_technical_indicators → validates entry/stop levels        │   │
│  │    get_price_history → checks target achievability               │   │
│  │    search_web("{ticker} earnings date upcoming")                 │   │
│  │    search_web("{ticker} macro risk next 4 weeks")                │   │
│  │    ... reasons and produces verdict JSON ...                     │   │
│  │                                                                  │   │
│  │  Validates JSON → AuditorVerdict Pydantic model                  │   │
│  │  Writes: reports/{run_id}/auditor_v{N}.json                      │   │
│  │                                                                  │   │
│  │  Posts to Slack as @SeniorAnalyst                                │   │
│  │  (SLACK_SENIOR_ANALYST_BOT_TOKEN):                               │   │──► Slack thread
│  │    "AUDITOR — $AAPL (Iteration N)                                │   │    @SeniorAnalyst posts
│  │     Decision: REVISION REQUESTED                                 │   │
│  │     Counter-signals: RSI divergence, earnings Mar 28 ..."        │   │
│  │                                                                  │   │
│  │  Returns to state:                                               │   │
│  │    auditor_verdict = { decision, critique, counter_signals, ...} │   │
│  │    revision_count = N                                            │   │
│  └──────────────────────────┬───────────────────────────────────────┘   │
│                             │                                           │
│                             ▼                                           │
│                   route_after_audit(state)                              │
│                             │                                           │
│              ┌──────────────┴──────────────┐                            │
│              │                             │                            │
│     decision=="approve"          decision=="revise"                     │
│     OR revision_count            AND revision_count                     │
│     >= MAX_REVISIONS             < MAX_REVISIONS                        │
│              │                             │                            │
│              ▼                             ▼                            │
│             END                     back to ANALYST                    │
│                                      (next iteration)                  │
└─────────────────────────────────────────────────────────────────────────┘
    │
    │  app.invoke() returns final state
    ▼
slack_bot.py
    │
    ├── _post_final_answer()
    │     posts as @StockGuru (SLACK_BOT_TOKEN):                ──► Slack thread
    │     "RECOMMENDATION: YES — Buy Apple (AAPL)               @StockGuru posts
    │      Buy limit: $210.00 | Target: $225.00 | Stop: $203.00
    │      R/R: 2.1x | Hold for: 2–3 weeks
    │      _{rationale}_"
    │
    └── _share_research_report()
          reads research_report.md
          POST to dpaste.com/api/v2/                            ──► dpaste.com
          receives URL                                          @StockGuru posts link
          posts link to Slack thread
```

Your code never calls node functions directly — only `app.invoke()`.

---

## 5. How Nodes Share Data — Contracts

Every node function has the same signature:

```python
def researcher_node(state: OverallState) -> dict:
    # read from state
    ticker = state["ticker"]
    # ... do work ...
    return {
        "researcher_report_path": path,   # only update these fields
        "trigger_signals": signals,
    }
```

The returned dict contains **only the fields this node produces**. LangGraph shallow-merges it into `OverallState` before calling the next node. This is the contract between nodes — they communicate through named state keys, not function arguments or return values.

### Two data-sharing patterns used in this codebase

| Pattern | Used for | Example |
|---|---|---|
| State dict | Structured, small data (decisions, prices, verdicts) | `analyst_thesis`, `auditor_verdict`, `revision_count` |
| Files on disk | Large text content that would bloat state | `research_report.md` |

For the research report, the Researcher writes the file to disk and puts only the **file path** into state (`researcher_report_path`). The Analyst and Auditor nodes read the file themselves using `file_memory.read_report(state["researcher_report_path"])`. This keeps state lean while still giving downstream nodes access to the full content.

Nodes can technically share data through any side channel (files, databases, global variables), but routing decisions must go through state because LangGraph only inspects state when evaluating edges. The recommended pattern is: **structured data and paths go in state, large content goes in files**.

---

## 6. Agents

### Researcher (`agents/researcher.py`)

The Researcher is a **custom node** — not a ReAct agent. It runs a fixed sequence of tool calls deterministically, then uses GPT-4o-mini to synthesize the raw data into a structured markdown report. There is no tool-calling loop; every tool always runs, and failures are caught and logged without stopping the pipeline.

**Model**: `gpt-4o-mini` (override: `RESEARCHER_MODEL`)

**Tool call sequence** (always all 7):
1. SEC EDGAR Form 4 insider filings
2. Unusual options flow (yfinance)
3. Reddit discussion spike (Perplexity)
4. Earnings transcript / guidance (Perplexity)
5. Analyst upgrades / news (Perplexity)
6. Price history — 3 months (yfinance)
7. Technical indicators — 50MA, 200MA, RSI(14), volume trend (yfinance)

**Report structure**: Key Trigger Signals · Insider Activity · Options Flow · Social & News Sentiment · Earnings & Guidance · Technical Picture · Key Risks

**Writes to state**: `researcher_report_path`, `trigger_signals`

---

### Analyst (`agents/analyst.py`)

The Analyst is a **ReAct agent** (`create_react_agent` from LangGraph prebuilt). It receives the research report and current market data, then reasons through a trade thesis using its tools in a loop until it produces a structured JSON output.

**Model**: `gpt-4o-mini` (override: `ANALYST_MODEL`)
**Tools**: `search_web`, `get_pe_ratio`, `get_price_history`, `get_technical_indicators`
**Focus**: Short-term only — max 3–4 week hold, limit orders, minimum 2:1 R/R ratio enforced in the prompt

On **first iteration**: reads the research report, fetches current technicals, builds an initial thesis.

On **revisions**: receives the SeniorAnalyst's counter-signals and is instructed to call `search_web` for each one explicitly before rewriting the thesis. This ensures revisions are evidence-based, not just acknowledgements.

**Always outputs a complete trade plan** regardless of BUY or PASS:

| Field | Description |
|---|---|
| `decision` | `BUY` or `PASS` |
| `entry_limit_price` | Limit buy price at or below current price, near a support level |
| `target_price` | Realistic profit target within the time horizon |
| `stop_loss` | Hard stop below key support; (target − entry) ≥ 2 × (entry − stop) |
| `time_horizon` | Maximum 4 weeks |
| `exit_conditions` | 3+ specific triggers (price levels, events, technical breaks) |
| `rationale` | 2–3 sentences covering signal, entry logic, and any auditor feedback addressed |

**Posts to Slack as**: `@Analyst` via `SLACK_ANALYST_BOT_TOKEN`
**Writes to state**: `analyst_thesis`

---

### SeniorAnalyst / Auditor (`agents/auditor.py`)

The SeniorAnalyst is a **ReAct agent** that acts as a risk manager stress-testing the Analyst's thesis. It uses tools to independently verify price levels and scan for near-term risks before rendering a verdict.

**Model**: `claude-haiku-4-5-20251001` (override: `AUDITOR_MODEL`; automatically falls back to GPT if the model name starts with `gpt`)
**Tools**: `search_web`, `get_price_history`, `get_technical_indicators`

**Audit checklist**:
1. **Price level validation** — is entry near real support? Is target achievable in the stated timeframe? Is R/R ≥ 2:1?
2. **Near-term risk scan** — upcoming earnings, Fed meetings, macro events within the trade window?
3. **Thesis logic check** — does BUY/PASS follow from the data? Are exit conditions specific and actionable? (On revisions: did the Analyst actually research the counter-signals, or just acknowledge them?)

On the **final iteration** (`revision_count == MAX_REVISIONS`), the prompt instructs the SeniorAnalyst to lean toward APPROVE if the thesis is directionally sound — ensuring the pipeline always terminates with a usable recommendation.

**Posts to Slack as**: `@SeniorAnalyst` via `SLACK_SENIOR_ANALYST_BOT_TOKEN`
**Writes to state**: `auditor_verdict`, incremented `revision_count`

---

## 7. Tools (`tools/`)

Tools are decorated with LangChain's `@tool` and passed to agent constructors. The Researcher calls them directly; the Analyst and SeniorAnalyst agents call them autonomously during their ReAct loops.

| Tool | File | Data source | Auth required |
|---|---|---|---|
| `search_web(query)` | `perplexity.py` | Perplexity `sonar` API | `PERPLEXITY_API_KEY` |
| `get_pe_ratio(ticker)` | `market_data.py` | yfinance | none |
| `get_price_history(ticker, period)` | `market_data.py` | yfinance | none |
| `get_technical_indicators(ticker)` | `market_data.py` | yfinance — 50MA, 200MA, RSI(14), volume ratio | none |
| `get_recent_form4_filings(ticker)` | `sec_filings.py` | SEC EDGAR public API | none (User-Agent header) |
| `get_unusual_options_flow(ticker)` | `options_flow.py` | yfinance options chain | none |

**Unusual options criterion**: `volume > 2× open_interest AND volume > 500` — top 20 strikes returned, sorted by volume/OI ratio.

---

## 8. Slack Bots

Three separate Slack apps are registered, each with its own bot token, so they appear as distinct identities in the thread.

| Slack identity | Token env var | Socket Mode | Role |
|---|---|---|---|
| StockGuru | `SLACK_BOT_TOKEN` + `SLACK_APP_TOKEN` | Yes (`xapp-...`) | Listens for messages; posts acknowledgement, final verdict, report link |
| Analyst | `SLACK_ANALYST_BOT_TOKEN` | No | Posts trade thesis after each Analyst iteration |
| SeniorAnalyst | `SLACK_SENIOR_ANALYST_BOT_TOKEN` | No | Posts audit verdict after each SeniorAnalyst iteration |

StockGuru is the only bot that needs Socket Mode because it's the only one that *receives* events. Analyst and SeniorAnalyst only ever *post* messages, so they just need a bot token with `chat:write` scope and must be invited to the channel.

All posting goes through `slack_client.post_message(text, thread_ts, token)`. The `token` parameter selects which bot identity posts the message. If a token isn't configured, the function falls back to `SLACK_BOT_TOKEN` and logs a warning.

**Message sequence per Slack thread**:
```
StockGuru    "Got it. Researching AAPL now. This takes 2–4 minutes."
Analyst      "ANALYST — $AAPL (Iteration 1) | Signal: BUY | Entry: $210.50 | ..."
SeniorAnalyst "AUDITOR — $AAPL (Iteration 1) | Decision: REVISION REQUESTED | ..."
Analyst      "ANALYST — $AAPL (Iteration 2) | Signal: BUY | Entry: $208.00 | ..."
SeniorAnalyst "AUDITOR — $AAPL (Iteration 2) | Decision: APPROVED | ..."
StockGuru    "RECOMMENDATION: YES — Buy Apple (AAPL) ..."
StockGuru    "Research report: https://dpaste.com/..."
```

---

## 9. File Layout

```
StockGuru/
├── main.py              — CLI entry point: python main.py AAPL
├── slack_bot.py         — Railway entry point: Slack Socket Mode listener
├── graph.py             — LangGraph StateGraph: nodes, edges, routing
├── state.py             — OverallState TypedDict + AnalystThesis + AuditorVerdict (Pydantic)
├── config.py            — All model IDs and env var constants (single source of truth)
├── slack_client.py      — post_message() wrapper supporting per-bot tokens
├── file_memory.py       — Helpers: write_report, read_report, write_json, read_json
│
├── agents/
│   ├── researcher.py    — Custom node: deterministic tool calls + GPT synthesis
│   ├── analyst.py       — ReAct agent: trade thesis builder (GPT-4o-mini)
│   └── auditor.py       — ReAct agent: risk reviewer (Claude Haiku)
│
├── tools/
│   ├── perplexity.py    — search_web via Perplexity API
│   ├── market_data.py   — get_pe_ratio, get_price_history, get_technical_indicators
│   ├── sec_filings.py   — get_recent_form4_filings via SEC EDGAR
│   └── options_flow.py  — get_unusual_options_flow via yfinance
│
├── reports/             — Auto-created at runtime, gitignored
│   └── AAPL_20260312_143022/
│       ├── research_report.md
│       ├── analyst_v1.json
│       ├── auditor_v1.json
│       └── analyst_v2.json   (only if a revision occurred)
│
├── Dockerfile           — python:3.13-slim, CMD: python slack_bot.py
├── railway.toml         — builder: dockerfile, restartPolicy: always
├── requirements.txt
├── .env.example
└── .env                 — gitignored, never committed
```

---

## 10. Configuration & Environment Variables

All model IDs and constants are read once in `config.py` and imported from there — no other file reads `os.getenv` directly. To swap a model, set the env var; no code changes needed.

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENAI_API_KEY` | Yes | — | Used by Researcher and Analyst (GPT-4o-mini) |
| `ANTHROPIC_API_KEY` | Yes | — | Used by SeniorAnalyst (Claude Haiku) |
| `PERPLEXITY_API_KEY` | Yes | — | Used by `search_web` tool |
| `SLACK_BOT_TOKEN` | Yes (Slack) | — | StockGuru bot token (`xoxb-...`) |
| `SLACK_APP_TOKEN` | Yes (Slack) | — | Socket Mode token (`xapp-...`) |
| `SLACK_CHANNEL_ID` | Yes (Slack) | — | Channel where bots post |
| `SLACK_ANALYST_BOT_TOKEN` | Yes (Slack) | — | Analyst bot token |
| `SLACK_SENIOR_ANALYST_BOT_TOKEN` | Yes (Slack) | — | SeniorAnalyst bot token |
| `RESEARCHER_MODEL` | No | `gpt-4o-mini` | LLM for research synthesis |
| `ANALYST_MODEL` | No | `gpt-4o-mini` | LLM for trade thesis |
| `AUDITOR_MODEL` | No | `claude-haiku-4-5-20251001` | LLM for risk audit |
| `PERPLEXITY_MODEL` | No | `sonar` | Perplexity model (`sonar-pro` for higher quality) |
| `MAX_REVISIONS` | No | `2` | Max Analyst↔SeniorAnalyst loops |
| `SEC_USER_AGENT` | No | `StockAnalyzer contact@example.com` | Required header for SEC EDGAR |

---

## 11. Running Locally

```bash
# Set up environment
python -m venv venv
source venv/Scripts/activate      # Windows Git Bash

pip install -r requirements.txt

cp .env.example .env
# fill in your API keys in .env

# CLI run — no Slack needed, prints result to terminal
python main.py AAPL

# Slack bot — connects to Slack via Socket Mode
python slack_bot.py
```

When running locally alongside a Railway deployment, only one instance should be active at a time — both will receive and process the same Slack events, causing duplicate responses.

---

## 12. Common Modifications

| Task | What to change |
|---|---|
| Add a new data tool | Create in `tools/`, decorate with `@tool`, add to the agent's tool list in `agents/*.py` |
| Add a new agent | Write a node function, register in `graph.py` with `add_node` + `add_edge` |
| Change the revision limit | Set `MAX_REVISIONS` env var, or change the default in `config.py` |
| Upgrade Analyst to GPT-4o | Set `ANALYST_MODEL=gpt-4o` |
| Upgrade SeniorAnalyst to Claude Sonnet | Set `AUDITOR_MODEL=claude-sonnet-4-6` |
| Add a new state field | Add to `OverallState` in `state.py`; initialize it in both `main.py` and `slack_bot.py` |
| Change loop routing logic | Edit `route_after_audit()` in `graph.py` |

---

## 13. FAQ

**Q: Is there a web server running on Railway?**
No. The container runs `slack_bot.py` as a long-lived process that holds an outbound WebSocket connection to Slack (Socket Mode). Slack pushes events to the container over that connection. No port is exposed, no HTTP server is started.

**Q: How does StockGuru know what stock to analyze from a natural language message?**
`extract_ticker()` in `slack_bot.py` calls GPT-4o-mini with a JSON-extraction prompt. It returns a ticker symbol and company name, or `(None, None)` if no stock is found. Using the ticker symbol directly (e.g. "AAPL") is more reliable than company names for lesser-known stocks.

**Q: What happens if a tool fails mid-pipeline (e.g. Perplexity is down)?**
Each tool call in the Researcher is wrapped in a try/except. Failures are logged with a clear `[RESEARCHER] ERROR: ...` message and replaced with a placeholder string so the pipeline continues. For the Analyst and SeniorAnalyst, tool failures bubble up through the ReAct loop — the agent may retry or work around the failure. If the agent itself fails (OpenAI/Anthropic API down), the error is logged and propagated to `slack_bot.py`, which posts the error message to the Slack thread.

**Q: Why does the Analyst always produce entry/target/stop prices even on a PASS decision?**
This is intentional. A PASS with no prices is useless — the user can't act on it. A PASS with specific prices tells you exactly what conditions would change the recommendation ("I'd buy at $205 with a target of $220 and stop at $198"). The SeniorAnalyst also uses these prices to validate the thesis even on a PASS.

**Q: Why does the SeniorAnalyst use Claude (Anthropic) while the others use GPT (OpenAI)?**
Different models have different strengths. Claude models tend to follow complex structured instructions more reliably, which matters for the auditor's multi-step checklist and JSON output requirement. Using a different provider for the auditor also provides a natural diversity of reasoning — the two models are less likely to share the same blind spots. The model is fully swappable via `AUDITOR_MODEL`.

**Q: Where do the research reports go? Are they stored permanently?**
Reports are written to the Railway container's local filesystem during a run and uploaded to `dpaste.com` at the end. The dpaste link is posted to Slack. The container filesystem is ephemeral — it's wiped on redeploy or restart. If you need permanent storage, the reports would need to be pushed to S3 or a database instead.

**Q: Can two users ask questions at the same time?**
Yes. Each incoming Slack message spawns a separate background thread. LangGraph's `app.invoke()` is stateless between calls — each invocation gets its own `OverallState` dict and its own `run_id` / `report_dir`. There is no shared mutable state between concurrent runs.

**Q: How do I add a fourth agent?**
1. Write a node function in `agents/yournewagent.py`
2. Add any new state fields to `OverallState` in `state.py` and initialize them in `main.py` + `slack_bot.py`
3. Register in `graph.py`: `workflow.add_node("newagent", new_agent_node)`
4. Add an edge from wherever it should receive control: `workflow.add_edge("auditor", "newagent")`
5. Update routing if needed
