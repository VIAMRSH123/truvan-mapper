"""Command line entry point.

    python -m mapper run                       # interactive review, offline baseline
    python -m mapper run --mode llm            # Claude proposes (needs ANTHROPIC_API_KEY)
    python -m mapper run --decisions demo.json # scripted reviewer, for demos/CI
    python -m mapper report                    # rebuild HTML from the ledger
    python -m mapper verify                    # check the ledger hash chain
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from langgraph.types import Command

from . import catalog
from .graph import build_graph
from .ledger import Ledger
from .proposers import make_proposer
from .report import render

DEFAULT_CONTROLS = ["A.6.2.4", "A.7.4", "A.6.2.8", "A.9.4", "A.8.4"]
FW = {"EU_AI_ACT": "EU AI Act", "NIST_AI_RMF": "NIST AI RMF"}


def _show(payload: dict) -> None:
    c = payload["control"]
    print(f"\n{'=' * 72}\n{c['id']}  {c['title']}   (attempt {payload['attempt']}/{payload['max_attempts']})")
    print(f"  {c['summary']}\n{'-' * 72}")
    if not payload["proposals"]:
        print("  (no valid proposals)")
    for i, p in enumerate(payload["proposals"]):
        print(f"  [{i}] {FW[p['framework']]:<12} {p['target_id']:<10} {p['relation']:<10} "
              f"conf {p['confidence']:.2f}\n      {p['rationale']}")
    for d in payload["dropped"]:
        print(f"  BLOCKED {d.get('framework')} {d.get('target_id')}: {d['reason']}")


def _ask(payload: dict, reviewer: str) -> dict:
    while True:
        a = input("\n[a]pprove all  [e]dit (keep some)  [r]eject with feedback > ").strip().lower()
        if a == "a":
            return {"action": "approve", "reviewer": reviewer}
        if a == "e":
            keep = input("Indexes to keep, comma separated: ")
            try:
                idx = [int(x) for x in keep.replace(" ", "").split(",") if x]
            except ValueError:
                print("Numbers only."); continue
            notes = input("Note (optional): ")
            return {"action": "edit", "keep": idx, "notes": notes, "reviewer": reviewer}
        if a == "r":
            notes = input("What is wrong? This goes back to the proposer: ").strip()
            if not notes:
                print("A reject needs a reason, so the next attempt can improve."); continue
            keep = input("Indexes to keep for next attempt (optional): ")
            try:
                idx = [int(x) for x in keep.replace(" ", "").split(",") if x]
            except ValueError:
                idx = []
            return {"action": "reject", "notes": notes, "keep": idx, "reviewer": reviewer}


def cmd_run(args) -> None:
    ledger = Ledger(args.ledger)
    graph = build_graph(make_proposer(args.mode), ledger, max_attempts=args.max_attempts)
    script = json.loads(Path(args.decisions).read_text()) if args.decisions else {}
    controls = [c.strip() for c in args.controls.split(",")] if args.controls else DEFAULT_CONTROLS
    if args.controls == "all":
        controls = [c["id"] for c in catalog.iso_controls()]

    for cid in controls:
        catalog.iso_control(cid)  # fail fast on a typo
        config = {"configurable": {"thread_id": f"{cid}"}}
        result = graph.invoke({"control_id": cid}, config)
        queue = list(script.get(cid, []))
        while "__interrupt__" in result:
            payload = result["__interrupt__"][0].value
            _show(payload)
            if script:
                decision = queue.pop(0) if queue else {"action": "approve"}
                decision.setdefault("reviewer", args.reviewer)
                print(f"\n  scripted reviewer -> {decision['action']}"
                      + (f": {decision['notes']}" if decision.get("notes") else ""))
            elif args.auto_approve:
                decision = {"action": "approve", "reviewer": args.reviewer + " (auto)"}
            else:
                decision = _ask(payload, args.reviewer)
            result = graph.invoke(Command(resume=decision), config)
        print(f"  saved: {cid} -> {result['status']} ({len(result['proposals'])} mappings)")

    out = render(ledger, args.report)
    ok, msg = ledger.verify()
    print(f"\nLedger: {msg}\nReport: {out}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="mapper", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--mode", choices=["baseline", "llm"], default="baseline")
    r.add_argument("--controls", help="comma-separated IDs, or 'all'")
    r.add_argument("--reviewer", default="reviewer")
    r.add_argument("--decisions", help="JSON file of scripted review decisions")
    r.add_argument("--auto-approve", action="store_true")
    r.add_argument("--max-attempts", type=int, default=3)
    r.add_argument("--ledger", default="out/ledger.jsonl")
    r.add_argument("--report", default="out/crosswalk.html")
    rp = sub.add_parser("report")
    rp.add_argument("--ledger", default="out/ledger.jsonl")
    rp.add_argument("--report", default="out/crosswalk.html")
    v = sub.add_parser("verify")
    v.add_argument("--ledger", default="out/ledger.jsonl")
    args = ap.parse_args(argv)

    if args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "report":
        print(render(Ledger(args.ledger), args.report))
    elif args.cmd == "verify":
        ok, msg = Ledger(args.ledger).verify()
        print(msg)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
