"""Deterministic guardrail between the proposer and the human reviewer.

An LLM can invent an article number that looks real. In compliance work
that is the worst failure: a fake citation in an audit pack. So nothing
reaches the reviewer unless its target ID exists in the shipped catalog.
"""
from __future__ import annotations

import math

from . import catalog
from .proposers import RELATIONS, Proposal


def validate(proposals: list[dict]) -> tuple[list[Proposal], list[dict]]:
    """Return (valid, dropped). Each dropped item carries a 'reason'."""
    index = catalog.target_index()
    valid: list[Proposal] = []
    dropped: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for p in proposals:
        fw, tid = p.get("framework"), str(p.get("target_id", "")).strip()
        if fw not in index:
            dropped.append({**p, "reason": f"unknown framework '{fw}'"})
            continue
        if tid not in index[fw]:
            dropped.append({**p, "reason": f"'{tid}' is not in the {fw} catalog (possible hallucination)"})
            continue
        if (fw, tid) in seen:
            dropped.append({**p, "reason": "duplicate"})
            continue
        if not str(p.get("rationale", "")).strip():
            dropped.append({**p, "reason": "missing rationale"})
            continue
        relation = p.get("relation") if p.get("relation") in RELATIONS else "supports"
        try:
            conf = float(p.get("confidence", 0))
        except (TypeError, ValueError):
            conf = 0.0
        if not math.isfinite(conf):      # NaN would survive min/max and reach the ledger as bare `NaN`
            conf = 0.0
        seen.add((fw, tid))
        valid.append(Proposal(
            framework=fw,
            target_id=tid,
            relation=relation,
            confidence=round(min(max(conf, 0.0), 1.0), 2),
            rationale=str(p["rationale"]).strip(),
        ))
    valid.sort(key=lambda p: (p["framework"], -p["confidence"]))
    return valid, dropped
