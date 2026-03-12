"""LangGraph StateGraph: researcher → analyst → auditor → loop."""
from __future__ import annotations

from langgraph.graph import StateGraph, START, END

import config
from state import OverallState
from agents.researcher import researcher_node
from agents.analyst import analyst_node
from agents.auditor import auditor_node


def route_after_audit(state: OverallState) -> str:
    """Route to 'analyst' for revision or 'end' if approved / max iterations reached."""
    verdict = state.get("auditor_verdict", {})
    revision_count = state.get("revision_count", 0)

    if verdict.get("decision") == "approve":
        print(f"\n[GRAPH] Auditor APPROVED thesis after {revision_count} iteration(s). Done.")
        return "end"

    if revision_count >= config.MAX_REVISIONS:
        print(f"\n[GRAPH] Max revisions ({config.MAX_REVISIONS}) reached. Ending pipeline.")
        return "end"

    print(f"\n[GRAPH] Auditor requested REVISION ({revision_count}/{config.MAX_REVISIONS}). Looping back to analyst.")
    return "analyst"


def build_graph():
    workflow = StateGraph(OverallState)

    workflow.add_node("researcher", researcher_node)
    workflow.add_node("analyst", analyst_node)
    workflow.add_node("auditor", auditor_node)

    workflow.add_edge(START, "researcher")
    workflow.add_edge("researcher", "analyst")
    workflow.add_edge("analyst", "auditor")
    workflow.add_conditional_edges(
        "auditor",
        route_after_audit,
        path_map={"analyst": "analyst", "end": END},
    )

    return workflow.compile()


# Module-level compiled graph (imported by main.py)
app = build_graph()
