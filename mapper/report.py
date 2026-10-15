"""Render the ledger as one self-contained HTML crosswalk report."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path

from . import catalog
from .ledger import Ledger

FW_LABEL = {"EU_AI_ACT": "EU AI Act", "NIST_AI_RMF": "NIST AI RMF"}

CSS = """
:root{--bg:#fbfaf7;--fg:#1b1b1a;--mute:#6b6a65;--line:#e4e1d9;--card:#fff;
--ok:#1f6f43;--ok-bg:#e6f2ea;--warn:#8a5a00;--warn-bg:#fbf0d9;--bad:#9b2c2c;--bad-bg:#f8e3e3;
--chip:#f1efe9;--accent:#2d3a8c}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--fg:#ecebe6;--mute:#9b9a94;--line:#2c2b28;
--card:#1c1c1a;--ok:#7fd1a0;--ok-bg:#16301f;--warn:#e9c27a;--warn-bg:#332710;--bad:#f0a3a3;
--bad-bg:#3a1c1c;--chip:#26251f;--accent:#9fb0ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1080px;margin:0 auto;padding:40px 16px 80px}
h1{font-size:28px;margin:0 0 4px;letter-spacing:-.01em}h2{font-size:18px;margin:40px 0 12px}
.sub{color:var(--mute);margin:0 0 24px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.stat b{display:block;font-size:24px}.stat span{color:var(--mute);font-size:13px}
.wrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--card)}
table{border-collapse:collapse;width:100%;min-width:640px}
th,td{text-align:left;vertical-align:top;padding:10px 12px;border-bottom:1px solid var(--line)}
th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--mute);font-weight:600}
tr:last-child td{border-bottom:0}
.id{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;white-space:nowrap}
.chip{display:inline-block;background:var(--chip);border-radius:6px;padding:2px 7px;margin:2px 4px 2px 0;
font-size:13px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.chip i{font-style:normal;color:var(--mute);font-family:ui-sans-serif,system-ui,sans-serif;font-size:11px;margin-left:4px}
.st{font-size:12px;font-weight:600;border-radius:999px;padding:2px 9px}
.approved{color:var(--ok);background:var(--ok-bg)}.edited{color:var(--warn);background:var(--warn-bg)}
.rejected{color:var(--bad);background:var(--bad-bg)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:14px}
.card h3{margin:0 0 4px;font-size:16px}.meta{color:var(--mute);font-size:13px;margin-bottom:10px}
.rat{color:var(--mute);font-size:14px}
details{margin-top:10px}summary{cursor:pointer;color:var(--accent);font-size:14px}
.att{border-left:2px solid var(--line);padding:4px 0 4px 12px;margin:10px 0;font-size:14px}
.drop{color:var(--bad);font-size:13px}
.seal{font-family:ui-monospace,monospace;font-size:12px;color:var(--mute);word-break:break-all}
footer{margin-top:48px;color:var(--mute);font-size:13px}
"""


def _chips(mappings, fw):
    items = [m for m in mappings if m["framework"] == fw]
    if not items:
        return '<span class="rat">-</span>'
    return "".join(
        f'<span class="chip">{escape(m["target_id"])}<i>{escape(m["relation"])} · {m["confidence"]:.2f}</i></span>'
        for m in items)


def render(ledger: Ledger, out: str | Path) -> Path:
    latest = ledger.latest_by_control()
    ok, chain_msg = ledger.verify()
    idx = catalog.target_index()
    order = [c["id"] for c in catalog.iso_controls()]
    rows = [latest[cid] for cid in order if cid in latest]

    n_map = sum(len(e["mappings"]) for e in rows)
    n_drop = sum(len(a.get("dropped", [])) for e in rows for a in e["history"])
    n_rej_rounds = sum(1 for e in rows for a in e["history"] if a["decision"].get("action") == "reject")
    proposers = sorted({e["proposer"] for e in rows})

    h = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
         f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
         f"<title>AI Control Crosswalk</title><style>{CSS}</style></head><body><main>",
         "<h1>AI Control Crosswalk</h1>",
         f"<p class='sub'>ISO/IEC 42001 Annex A mapped to the EU AI Act and NIST AI RMF. "
         f"Every mapping below was approved by a human reviewer. Proposer: {escape(', '.join(proposers) or '-')}.</p>",
         "<div class='stats'>",
         f"<div class='stat'><b>{len(rows)}</b><span>controls reviewed</span></div>",
         f"<div class='stat'><b>{n_map}</b><span>approved mappings</span></div>",
         f"<div class='stat'><b>{n_rej_rounds}</b><span>proposals sent back by reviewer</span></div>",
         f"<div class='stat'><b>{n_drop}</b><span>invalid IDs blocked by validator</span></div>",
         f"<div class='stat'><b>{'Intact' if ok else 'BROKEN'}</b><span>audit ledger hash chain</span></div>",
         "</div>",
         "<h2>Crosswalk</h2><div class='wrap'><table><thead><tr>"
         "<th>ISO 42001 control</th><th>EU AI Act</th><th>NIST AI RMF</th><th>Status</th></tr></thead><tbody>"]
    for e in rows:
        h.append(
            f"<tr><td><span class='id'>{escape(e['control_id'])}</span><br>{escape(e['control_title'])}</td>"
            f"<td>{_chips(e['mappings'], 'EU_AI_ACT')}</td><td>{_chips(e['mappings'], 'NIST_AI_RMF')}</td>"
            f"<td><span class='st {e['status']}'>{e['status']}</span></td></tr>")
    h.append("</tbody></table></div>")

    h.append("<h2>Rationale and audit trail</h2>")
    for e in rows:
        h.append(f"<div class='card'><h3><span class='id'>{escape(e['control_id'])}</span> "
                 f"{escape(e['control_title'])} <span class='st {e['status']}'>{e['status']}</span></h3>"
                 f"<div class='meta'>Reviewed by {escape(e['reviewer'])} · {escape(e['timestamp'])} · "
                 f"{e['attempts']} attempt(s)</div>")
        if e["mappings"]:
            h.append("<table><thead><tr><th>Target</th><th>Relation</th><th>Conf.</th><th>Why</th></tr></thead><tbody>")
            for m in e["mappings"]:
                title = idx[m["framework"]][m["target_id"]]["title"]
                h.append(f"<tr><td><span class='id'>{FW_LABEL[m['framework']]} {escape(m['target_id'])}</span>"
                         f"<br><span class='rat'>{escape(title)}</span></td><td>{escape(m['relation'])}</td>"
                         f"<td>{m['confidence']:.2f}</td><td class='rat'>{escape(m['rationale'])}</td></tr>")
            h.append("</tbody></table>")
        else:
            h.append("<p class='rat'>No mapping accepted.</p>")
        if e.get("notes"):
            h.append(f"<p><b>Reviewer note:</b> {escape(e['notes'])}</p>")
        h.append("<details><summary>Attempt history</summary>")
        for a in e["history"]:
            d = a["decision"]
            props = ", ".join(f"{FW_LABEL[p['framework']]} {p['target_id']}" for p in a["proposals"]) or "none"
            h.append(f"<div class='att'><b>Attempt {a['attempt']}</b> ({escape(a['proposer'])}): {escape(props)}"
                     f"<br>Decision: <b>{escape(d.get('action', '?'))}</b>"
                     + (f" - {escape(d['notes'])}" if d.get("notes") else ""))
            for dr in a.get("dropped", []):
                h.append(f"<br><span class='drop'>Blocked: {escape(str(dr.get('framework')))} "
                         f"{escape(str(dr.get('target_id')))} - {escape(dr['reason'])}</span>")
            h.append("</div>")
        h.append(f"</details><div class='seal'>sha256 {e['hash']}</div></div>")

    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    h.append(f"<footer>Generated {gen}. Ledger check: {escape(chain_msg)}. ISO/IEC 42001 summaries are "
             f"paraphrased; the published standard governs exact wording. This is a prototype, "
             f"not legal advice.</footer></main></body></html>")
    out = Path(out)
    out.write_text("\n".join(h), encoding="utf-8")
    return out
