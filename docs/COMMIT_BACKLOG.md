# Daily Commit Backlog

A queue of real work for this repo, highest priority first. A scheduled Claude Code agent takes the
**top unchecked item** on its rotation day, does it properly, verifies it, and commits. Every item
below was checked against the code on 2026-10-19 (baseline: 12 tests passing).

## The rules the daily agent follows

1. **One item per run.** Finish it or leave it uncommitted. Never commit a half-done item.
2. **Verify before committing.** `pip install -r requirements.txt && python -m pytest -q` must be
   green. Tests must run offline with no API key (use `FakeProposer` / a fake Anthropic client).
3. **No filler commits.** No whitespace churn, comment padding, reformatting untouched files, or
   docs edits with no substance. If no item can be done honestly, make no commit and say so.
4. **Never commit secrets** or a regenerated `out/` / `docs/report.png` unless the item says so.
   Never use `git add -A` or `git add .`; stage named paths only.
5. **Respect the design decisions in the README**: the LLM is never trusted directly, a reject
   carries a reason, nothing reaches the ledger without a named reviewer. Do not weaken these.
6. **Conventional commit messages** (`fix:`, `test:`, `feat:`, `docs:`), body explains *why*.
7. **Tick the item and append to the Log** below in the same commit.

---

## Queue

### A. Bugs found in the audit (fix first, with a regression test each)

- [x] **A1 — `Ledger.verify()` crashes on a damaged file instead of reporting it.** A truncated or
      garbled line (e.g. a crash mid-write) makes `entries()` raise `json.JSONDecodeError`, so
      `python -m mapper verify` dies with a traceback rather than printing a failure and exiting 1.
      An entry missing `prev_hash`/`hash`/`seq` raises `KeyError` the same way. A tamper-evident
      ledger must fail *cleanly* on tampering. Make `verify()` return `(False, "...")` for both
      (naming the line number), keep `entries()` strict for normal reads, and test it.
- [ ] **A2 — Validator lets `NaN` confidence through.** `float("nan")` survives
      `min(max(conf, 0.0), 1.0)` unchanged, so a `NaN` reaches the ledger (`json.dumps` emits the
      non-standard token `NaN`) and the report. Treat non-finite confidence as `0.0`, like the
      existing non-numeric handling. Add a test with `nan` and `inf`.
- [ ] **A3 — `edit` with out-of-range or negative `keep` indexes silently saves an empty
      "edited" entry.** Verified: `keep: [99, -1]` on a 4-proposal control records status `edited`
      with zero mappings. Reject the decision (or ignore invalid indexes and refuse an empty edit)
      so a typo can't erase a control's mappings from the audit record. Cover `edit` and the
      `reject`-pin path in tests.
- [ ] **A4 — Graph accepts `reject` with no notes.** The CLI insists on a reason but
      `build_graph` does not: a scripted/other caller can resume with `{"action": "reject"}` and the
      proposer gets the placeholder "Rejected without notes." The README says a reject needs a
      reason. Enforce it in the `human_review` node (raise a clear `ValueError`) and test it.

### B. Test coverage gaps

`mapper/cli.py` (130 lines) has no tests; `report.py` has one smoke test.

- [ ] **B1 — CLI tests** (`tests/test_cli.py`): `run --decisions demo/decisions.json` end to end in
      `tmp_path` (statuses approved/edited/rejected-then-approved), `--auto-approve`, an unknown
      `--controls` ID failing fast, `verify` exit code 0 on a clean ledger and 1 on a tampered one,
      and `report` rebuilding HTML from an existing ledger.
- [ ] **B2 — Catalog integrity test.** Assert 38 unique ISO control IDs, 16 EU articles, 19 NIST
      categories (the numbers the README states), every item has `id`/`title`/`summary`/`keywords`,
      and every `DEFAULT_CONTROLS` ID in `cli.py` exists. Catches a data edit that breaks the
      README's claims.
- [ ] **B3 — Report rendering tests.** HTML-escaping of reviewer notes and rationale (inject
      `<script>`), the BROKEN state when the ledger is tampered, a `rejected` entry rendering "No
      mapping accepted", and that a later review of the same control supersedes the earlier one.
- [ ] **B4 — `ClaudeProposer` edge cases.** Feedback and the exclude list appear in the prompt,
      a response with no `tool_use` block returns `[]`, and `make_proposer("llm")` exits with the
      documented message when `ANTHROPIC_API_KEY` is unset.
- [ ] **B5 — Ledger behaviour.** `seq` increments, `latest_by_control` returns the last decision,
      blank lines are tolerated, and the first entry chains from `GENESIS`.

### C. Features from the README's "Limits and next steps"

- [ ] **C1 — Persistent checkpointer.** Swap `InMemorySaver` for the SQLite saver
      (`langgraph-checkpoint-sqlite`) behind a `--checkpoint-db` flag so a review survives a
      restart. Add a test that pauses, rebuilds the graph and resumes.
- [ ] **C2 — Benchmark script.** `python -m mapper benchmark --reference ref.json` computing
      precision/recall per framework for a proposer against a reference crosswalk. The reference
      must be supplied by a human; do not invent one. Needs a documented JSON schema first.
- [ ] **C3 — Evidence layer.** Attach artifact references (path + sha256) to approved mappings.
      Needs a design note in the README before code.

### D. Docs

- [ ] **D1 — README test count.** README says "12 tests"; update it whenever the count changes
      (do it inside the item that changes it, not as a standalone commit).

---

## Log

Append one line per commit: `YYYY-MM-DD - <item id> - <commit subject>`. The line goes in the same
commit as the work, so the sha isn't known yet; leave it out.

- 2026-10-19 - setup - docs: add commit backlog from a verified audit of the repo
- 2026-10-21 - A1 - fix: make Ledger.verify() report damaged ledger files instead of crashing
