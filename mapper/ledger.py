"""Append-only, hash-chained ledger of reviewed mappings.

Each line in the JSONL file holds one human decision. Each entry stores
the hash of the previous entry, so a silent edit to an old line breaks
the chain and `verify()` reports it. Auditors care about this more than
about the mappings themselves.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64


def _digest(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def entries(self) -> list[dict]:
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(l) for l in lines if l.strip()]

    def _last_hash(self) -> str:
        e = self.entries()
        return e[-1]["hash"] if e else GENESIS

    def record(self, *, control, status, mappings, reviewer, notes, proposer, history):
        entry = {
            "seq": len(self.entries()) + 1,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "control_id": control["id"],
            "control_title": control["title"],
            "status": status,
            "mappings": mappings,
            "reviewer": reviewer,
            "notes": notes,
            "proposer": proposer,
            "attempts": len(history),
            "history": history,
            "prev_hash": self._last_hash(),
        }
        entry["hash"] = _digest(entry)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def verify(self) -> tuple[bool, str]:
        prev = GENESIS
        for e in self.entries():
            if e["prev_hash"] != prev:
                return False, f"chain broken before seq {e['seq']}"
            if _digest(e) != e["hash"]:
                return False, f"entry seq {e['seq']} was modified"
            prev = e["hash"]
        return True, f"{len(self.entries())} entries, chain intact"

    def latest_by_control(self) -> dict[str, dict]:
        """Last decision per control (a later review supersedes an earlier one)."""
        out: dict[str, dict] = {}
        for e in self.entries():
            out[e["control_id"]] = e
        return out
