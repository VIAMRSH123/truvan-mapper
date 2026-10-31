import json
from types import SimpleNamespace

import pytest
from langgraph.types import Command

from mapper import catalog
from mapper.graph import build_graph
from mapper.ledger import Ledger
from mapper.proposers import BaselineProposer, ClaudeProposer
from mapper.report import render
from mapper.validate import validate


class FakeProposer:
    """Returns a fixed list, records what feedback it was given."""
    name = "fake"

    def __init__(self, batches):
        self.batches = list(batches)
        self.calls = []

    def propose(self, control, feedback=None, exclude=frozenset()):
        self.calls.append({"feedback": feedback, "exclude": set(exclude)})
        return self.batches.pop(0) if self.batches else []


GOOD = {"framework": "EU_AI_ACT", "target_id": "Art. 15", "relation": "partial",
        "confidence": 0.8, "rationale": "Both require accuracy testing."}
GOOD2 = {"framework": "NIST_AI_RMF", "target_id": "MEASURE 2", "relation": "partial",
         "confidence": 0.7, "rationale": "Trustworthiness evaluation."}
FAKE = {"framework": "EU_AI_ACT", "target_id": "Art. 99", "relation": "equivalent",
        "confidence": 0.99, "rationale": "Looks plausible but does not exist."}


def run(graph, cid, decisions):
    cfg = {"configurable": {"thread_id": cid}}
    res = graph.invoke({"control_id": cid}, cfg)
    for d in decisions:
        assert "__interrupt__" in res, "graph saved before the human decided"
        res = graph.invoke(Command(resume=d), cfg)
    return res


# ---------- validator ----------

def test_validator_blocks_hallucinated_ids_and_duplicates():
    valid, dropped = validate([GOOD, FAKE, GOOD, {**GOOD2, "confidence": 7}])
    assert [p["target_id"] for p in valid] == ["Art. 15", "MEASURE 2"]
    assert valid[1]["confidence"] == 1.0                      # clamped
    reasons = " ".join(d["reason"] for d in dropped)
    assert "Art. 99" in reasons and "hallucination" in reasons
    assert "duplicate" in reasons


def test_validator_requires_rationale_and_fixes_bad_relation():
    valid, dropped = validate([{**GOOD, "rationale": " "},
                               {**GOOD2, "relation": "same-ish"}])
    assert len(dropped) == 1 and dropped[0]["reason"] == "missing rationale"
    assert valid[0]["relation"] == "supports"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), "nan", "inf"])
def test_validator_treats_non_finite_confidence_as_zero(bad):
    valid, _ = validate([{**GOOD, "confidence": bad}])
    assert valid[0]["confidence"] == 0.0
    json.loads(json.dumps(valid[0]), parse_constant=lambda c: pytest.fail(c))


# ---------- human in the loop ----------

def test_graph_pauses_and_writes_nothing_before_review(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    g = build_graph(FakeProposer([[GOOD, FAKE]]), ledger)
    res = g.invoke({"control_id": "A.6.2.4"}, {"configurable": {"thread_id": "t"}})
    payload = res["__interrupt__"][0].value
    assert [p["target_id"] for p in payload["proposals"]] == ["Art. 15"]
    assert payload["dropped"][0]["target_id"] == "Art. 99"
    assert ledger.entries() == []


def test_approve_saves(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    g = build_graph(FakeProposer([[GOOD, GOOD2]]), ledger)
    res = run(g, "A.6.2.4", [{"action": "approve", "reviewer": "vb"}])
    assert res["status"] == "approved"
    (e,) = ledger.entries()
    assert e["reviewer"] == "vb" and len(e["mappings"]) == 2


def test_edit_keeps_only_selected(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    g = build_graph(FakeProposer([[GOOD, GOOD2]]), ledger)
    run(g, "A.6.2.4", [{"action": "edit", "keep": [1], "reviewer": "vb"}])
    (e,) = ledger.entries()
    assert e["status"] == "edited"
    assert [m["target_id"] for m in e["mappings"]] == ["MEASURE 2"]


def test_reject_sends_feedback_and_exclusions_back(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    fake = FakeProposer([[GOOD], [GOOD2]])
    g = build_graph(fake, ledger)
    run(g, "A.6.2.4", [{"action": "reject", "notes": "Art. 15 is too narrow"},
                       {"action": "approve"}])
    assert fake.calls[1]["feedback"] == "Art. 15 is too narrow"
    assert ("EU_AI_ACT", "Art. 15") in fake.calls[1]["exclude"]
    (e,) = ledger.entries()
    assert e["attempts"] == 2 and e["mappings"][0]["target_id"] == "MEASURE 2"


def test_partial_reject_pins_kept_proposals(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    fake = FakeProposer([[GOOD, GOOD2], [FAKE]])
    g = build_graph(fake, ledger)
    # sorted order: EU Art. 15 [0], NIST MEASURE 2 [1]; keep NIST, redo EU
    run(g, "A.6.2.4", [{"action": "reject", "keep": [1], "notes": "redo EU side"},
                       {"action": "approve"}])
    assert ("EU_AI_ACT", "Art. 15") in fake.calls[1]["exclude"]
    (e,) = ledger.entries()
    assert [m["target_id"] for m in e["mappings"]] == ["MEASURE 2"]   # FAKE was blocked
    assert e["history"][1]["dropped"][0]["target_id"] == "Art. 99"


def test_max_attempts_ends_as_rejected(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    g = build_graph(FakeProposer([[GOOD], [GOOD2]]), ledger, max_attempts=2)
    res = run(g, "A.6.2.4", [{"action": "reject", "notes": "no"},
                             {"action": "reject", "notes": "still no"}])
    assert res["status"] == "rejected"
    assert ledger.entries()[0]["mappings"] == []


def test_baseline_does_not_repeat_rejected_targets():
    b = BaselineProposer()
    ctrl = catalog.iso_control("A.7.4")
    first = {(p["framework"], p["target_id"]) for p in b.propose(ctrl)}
    second = {(p["framework"], p["target_id"]) for p in b.propose(ctrl, exclude=first)}
    assert first and not (first & second)


# ---------- ledger ----------

def test_ledger_detects_tampering(tmp_path):
    path = tmp_path / "l.jsonl"
    ledger = Ledger(path)
    g = build_graph(FakeProposer([[GOOD], [GOOD2]]), ledger)
    run(g, "A.6.2.4", [{"action": "approve"}])
    run(g, "A.7.4", [{"action": "approve"}])
    assert ledger.verify()[0]
    lines = path.read_text().splitlines()
    first = json.loads(lines[0]); first["reviewer"] = "someone-else"
    path.write_text("\n".join([json.dumps(first, sort_keys=True), lines[1]]) + "\n")
    ok, msg = ledger.verify()
    assert not ok and "seq 1" in msg


def _two_entry_ledger(tmp_path):
    path = tmp_path / "l.jsonl"
    ledger = Ledger(path)
    g = build_graph(FakeProposer([[GOOD], [GOOD2]]), ledger)
    run(g, "A.6.2.4", [{"action": "approve"}])
    run(g, "A.7.4", [{"action": "approve"}])
    return ledger, path


def test_verify_reports_truncated_line_instead_of_raising(tmp_path):
    ledger, path = _two_entry_ledger(tmp_path)
    lines = path.read_text().splitlines()
    path.write_text(lines[0] + "\n" + lines[1][:40] + "\n")
    ok, msg = ledger.verify()
    assert not ok and "line 2" in msg and "not valid JSON" in msg


def test_verify_reports_entry_missing_chain_fields(tmp_path):
    ledger, path = _two_entry_ledger(tmp_path)
    lines = path.read_text().splitlines()
    second = json.loads(lines[1]); del second["prev_hash"]
    path.write_text(lines[0] + "\n" + json.dumps(second) + "\n")
    ok, msg = ledger.verify()
    assert not ok and "line 2" in msg and "malformed" in msg


def test_report_renders(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    g = build_graph(FakeProposer([[GOOD, FAKE]]), ledger)
    run(g, "A.6.2.4", [{"action": "approve", "reviewer": "vb"}])
    html = render(ledger, tmp_path / "r.html").read_text()
    assert "A.6.2.4" in html and "Art. 15" in html and "Intact" in html
    assert "Blocked" in html and "Art. 99" in html


# ---------- LLM proposer (no network) ----------

def test_claude_proposer_reads_forced_tool_call():
    captured = {}

    class FakeMessages:
        def create(self, **kw):
            captured.update(kw)
            block = SimpleNamespace(type="tool_use", input={"mappings": [GOOD, FAKE]})
            return SimpleNamespace(content=[block])

    p = ClaudeProposer(model="test-model", client=SimpleNamespace(messages=FakeMessages()))
    out = p.propose(catalog.iso_control("A.6.2.4"), feedback="be stricter",
                    exclude={("EU_AI_ACT", "Art. 17")})
    assert captured["tool_choice"]["name"] == "submit_mappings"
    prompt = captured["messages"][0]["content"]
    assert "be stricter" in prompt and "EU_AI_ACT Art. 17" in prompt
    valid, dropped = validate(out)
    assert len(valid) == 1 and len(dropped) == 1
