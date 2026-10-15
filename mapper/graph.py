"""LangGraph workflow: propose -> validate -> human review -> save | revise.

    START -> propose -> validate -> human_review --approve/edit--> save -> END
                ^                        |
                +------- reject ---------+   (up to max_attempts, then saved as "rejected")

The human_review node calls langgraph.types.interrupt(). The graph pauses,
the checkpointer stores state, and the caller resumes with
Command(resume={...decision...}). Nothing is written to the ledger
without an explicit human decision.
"""
from __future__ import annotations

from typing import Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from . import catalog
from .ledger import Ledger
from .proposers import Proposal, Proposer
from .validate import validate


class Decision(TypedDict, total=False):
    action: Literal["approve", "edit", "reject"]
    keep: list[int]          # edit: indexes to save; reject: indexes to pin for next attempt
    notes: str               # reviewer reasoning, required for reject
    reviewer: str


class MapState(TypedDict, total=False):
    control_id: str
    attempt: int
    proposals: list[Proposal]
    dropped: list[dict]
    feedback: str | None
    excluded: list[list[str]]        # [[framework, target_id], ...] already rejected
    pinned: list[Proposal]           # kept by reviewer through a partial reject
    history: list[dict]              # every attempt and decision, for the audit trail
    decision: Decision
    status: str                      # approved | edited | rejected


def build_graph(proposer: Proposer, ledger: Ledger, max_attempts: int = 3,
                checkpointer=None):

    def propose(state: MapState) -> MapState:
        control = catalog.iso_control(state["control_id"])
        exclude = {tuple(x) for x in state.get("excluded", [])}
        raw = proposer.propose(control, state.get("feedback"), exclude)
        # Proposals the reviewer pinned on a partial reject come back unchanged.
        return {"proposals": list(state.get("pinned", [])) + list(raw),
                "attempt": state.get("attempt", 0) + 1}

    def check(state: MapState) -> MapState:
        valid, dropped = validate(state.get("proposals", []))
        return {"proposals": valid, "dropped": dropped}

    def human_review(state: MapState) -> Command:
        control = catalog.iso_control(state["control_id"])
        decision: Decision = interrupt({
            "control": control,
            "attempt": state["attempt"],
            "max_attempts": max_attempts,
            "proposals": state["proposals"],
            "dropped": state.get("dropped", []),
        })
        action = decision.get("action")
        history = state.get("history", []) + [{
            "attempt": state["attempt"],
            "proposer": proposer.name,
            "proposals": state["proposals"],
            "dropped": state.get("dropped", []),
            "decision": decision,
        }]

        if action in ("approve", "edit"):
            return Command(goto="save", update={"decision": decision, "history": history})

        if action == "reject":
            # "keep" on a reject pins the good proposals; only the rest are excluded.
            keep = set(decision.get("keep", []))
            pinned = [p for i, p in enumerate(state["proposals"]) if i in keep]
            excluded = state.get("excluded", []) + [
                [p["framework"], p["target_id"]]
                for i, p in enumerate(state["proposals"]) if i not in keep]
            excluded += [[p["framework"], p["target_id"]] for p in pinned]  # don't re-propose pinned
            if state["attempt"] >= max_attempts:
                return Command(goto="save", update={
                    "decision": decision, "history": history, "excluded": excluded})
            return Command(goto="propose", update={
                "feedback": decision.get("notes") or "Rejected without notes.",
                "excluded": excluded, "pinned": pinned, "history": history})

        raise ValueError(f"Unknown review action: {action!r}")

    def save(state: MapState) -> MapState:
        d = state["decision"]
        props = state["proposals"]
        if d["action"] == "edit":
            keep = set(d.get("keep", []))
            props = [p for i, p in enumerate(props) if i in keep]
            status = "edited"
        elif d["action"] == "approve":
            status = "approved"
        else:
            props, status = [], "rejected"
        ledger.record(
            control=catalog.iso_control(state["control_id"]),
            status=status,
            mappings=props,
            reviewer=d.get("reviewer", "unknown"),
            notes=d.get("notes", ""),
            proposer=proposer.name,
            history=state.get("history", []),
        )
        return {"status": status, "proposals": props}

    g = StateGraph(MapState)
    g.add_node("propose", propose)
    g.add_node("validate", check)
    g.add_node("human_review", human_review)
    g.add_node("save", save)
    g.add_edge(START, "propose")
    g.add_edge("propose", "validate")
    g.add_edge("validate", "human_review")
    g.add_edge("save", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())
