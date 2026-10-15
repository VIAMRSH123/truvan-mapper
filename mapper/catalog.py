"""Load the three framework catalogs shipped in mapper/data."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

SOURCE = "ISO42001"
TARGETS = {
    "EU_AI_ACT": "eu_ai_act.json",
    "NIST_AI_RMF": "nist_ai_rmf.json",
}


@lru_cache(maxsize=None)
def _load(filename: str) -> dict:
    return json.loads((DATA_DIR / filename).read_text(encoding="utf-8"))


def iso_controls() -> list[dict]:
    return _load("iso42001_annex_a.json")["items"]


def iso_control(control_id: str) -> dict:
    for item in iso_controls():
        if item["id"] == control_id:
            return item
    raise KeyError(f"Unknown ISO 42001 Annex A control: {control_id}")


def target_items(framework: str) -> list[dict]:
    return _load(TARGETS[framework])["items"]


def target_index() -> dict[str, dict[str, dict]]:
    """{framework: {item_id: item}} for fast validation."""
    return {fw: {i["id"]: i for i in target_items(fw)} for fw in TARGETS}


def framework_label(framework: str) -> str:
    if framework == SOURCE:
        return _load("iso42001_annex_a.json")["framework"]
    return _load(TARGETS[framework])["framework"]
