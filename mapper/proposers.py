"""Proposers suggest candidate mappings for one ISO 42001 control.

Two implementations share one interface:
- BaselineProposer: offline, explainable keyword overlap. No API key needed.
- ClaudeProposer: an LLM reads the control and both target catalogs and
  returns structured mappings through a forced tool call.

Neither proposer is trusted. Every proposal goes through the validator and
a human reviewer before it is saved.
"""
from __future__ import annotations

import json
import math
import os
import re
from typing import Protocol, TypedDict

from . import catalog


class Proposal(TypedDict):
    framework: str          # "EU_AI_ACT" | "NIST_AI_RMF"
    target_id: str          # e.g. "Art. 10", "MEASURE 2"
    relation: str           # "equivalent" | "partial" | "supports"
    confidence: float       # 0..1
    rationale: str


RELATIONS = ("equivalent", "partial", "supports")


class Proposer(Protocol):
    name: str

    def propose(self, control: dict, feedback: str | None,
                exclude: set[tuple[str, str]]) -> list[Proposal]: ...


# --------------------------------------------------------------------------
# Offline baseline
# --------------------------------------------------------------------------
_WORD = re.compile(r"[a-z][a-z\-]+")
_STOP = {"the", "and", "for", "of", "to", "a", "an", "in", "on", "with", "ai",
         "system", "systems", "across", "their", "its", "are", "is", "be",
         "so", "that", "per", "use"}


def _terms(item: dict) -> set[str]:
    text = " ".join([item["title"], item.get("summary", "")]).lower()
    words = {w for w in _WORD.findall(text) if w not in _STOP and len(w) > 2}
    return words | {k.lower() for k in item.get("keywords", [])}


class BaselineProposer:
    """Keyword-overlap matcher. Transparent and deterministic, not clever."""

    name = "baseline-keyword-v1"

    def __init__(self, per_framework: int = 2, min_score: float = 0.35):
        self.per_framework = per_framework
        self.min_score = min_score

    def propose(self, control, feedback=None, exclude=frozenset()):
        c_terms = _terms(control)
        c_kw = {k.lower() for k in control.get("keywords", [])}
        out: list[Proposal] = []
        for fw in catalog.TARGETS:
            scored = []
            for item in catalog.target_items(fw):
                if (fw, item["id"]) in exclude:
                    continue
                t_kw = {k.lower() for k in item.get("keywords", [])}
                shared_kw = c_kw & t_kw
                shared_any = (c_terms & _terms(item)) - shared_kw
                # Curated keyword matches count fully, incidental word matches half.
                raw = len(shared_kw) + 0.5 * len(shared_any)
                score = raw / math.sqrt(max(len(t_kw), 1))
                if score >= self.min_score and shared_kw:
                    scored.append((score, item, sorted(shared_kw | shared_any)))
            scored.sort(key=lambda s: s[0], reverse=True)
            for score, item, shared in scored[: self.per_framework]:
                conf = round(min(0.85, 0.3 + score / 3), 2)
                out.append(Proposal(
                    framework=fw,
                    target_id=item["id"],
                    relation="partial" if score >= 1.0 else "supports",
                    confidence=conf,
                    rationale=f"Shared terms: {', '.join(shared[:6])}.",
                ))
        return out


# --------------------------------------------------------------------------
# LLM proposer (Claude)
# --------------------------------------------------------------------------
_TOOL = {
    "name": "submit_mappings",
    "description": "Submit candidate mappings from one ISO/IEC 42001 Annex A "
                   "control to EU AI Act articles and NIST AI RMF categories.",
    "input_schema": {
        "type": "object",
        "properties": {
            "mappings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "framework": {"type": "string",
                                      "enum": list(catalog.TARGETS)},
                        "target_id": {"type": "string"},
                        "relation": {"type": "string", "enum": list(RELATIONS)},
                        "confidence": {"type": "number"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["framework", "target_id", "relation",
                                 "confidence", "rationale"],
                },
            }
        },
        "required": ["mappings"],
    },
}

_SYSTEM = """You are a compliance analyst who maps AI governance controls.
Rules:
- Use ONLY target IDs that appear in the catalogs you are given. Never invent IDs.
- Prefer 1-3 strong mappings per framework over many weak ones. Zero is allowed.
- relation: "equivalent" only if the target requires substantially the same thing;
  "partial" if it covers part of it; "supports" if it helps produce evidence for it.
- confidence is 0..1 and must reflect real uncertainty.
- rationale: one or two plain sentences on what overlaps and what does not.
- If reviewer feedback is given, address it directly."""


class ClaudeProposer:
    name = "claude"

    def __init__(self, model: str | None = None, client=None):
        self.model = model or os.environ.get("TRUVAN_MODEL", "claude-sonnet-5-5")
        self.name = f"claude:{self.model}"
        if client is None:
            import anthropic  # imported lazily so baseline mode needs no key
            client = anthropic.Anthropic()
        self.client = client

    def _prompt(self, control, feedback, exclude) -> str:
        cats = {fw: [{k: i[k] for k in ("id", "title", "summary")}
                     for i in catalog.target_items(fw)]
                for fw in catalog.TARGETS}
        parts = [
            "SOURCE CONTROL (ISO/IEC 42001 Annex A):",
            json.dumps({k: control[k] for k in ("id", "objective", "title", "summary")}, indent=2),
            "\nTARGET CATALOGS:",
            json.dumps(cats, indent=2),
        ]
        if exclude:
            parts.append("\nDo NOT propose these (already rejected): "
                         + ", ".join(f"{fw} {tid}" for fw, tid in sorted(exclude)))
        if feedback:
            parts.append(f"\nREVIEWER FEEDBACK ON YOUR LAST ATTEMPT:\n{feedback}")
        return "\n".join(parts)

    def propose(self, control, feedback=None, exclude=frozenset()):
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=1500,
            system=_SYSTEM,
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": "submit_mappings"},
            messages=[{"role": "user", "content": self._prompt(control, feedback, exclude)}],
        )
        for block in msg.content:
            if getattr(block, "type", None) == "tool_use":
                return list(block.input.get("mappings", []))
        return []


def make_proposer(mode: str) -> Proposer:
    if mode == "baseline":
        return BaselineProposer()
    if mode == "llm":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("LLM mode needs ANTHROPIC_API_KEY. "
                             "Use --mode baseline to run offline.")
        return ClaudeProposer()
    raise ValueError(mode)
