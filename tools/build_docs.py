#!/usr/bin/env python3
"""Generate the per-workflow READMEs and the tables in the root README.

    python tools/build_docs.py          # write
    python tools/build_docs.py --check  # fail if any generated file is out of date

Hand-written text (summary, what it does, design notes, limitations) lives in
catalog.toml. Everything else is computed from workflow.json, so the docs cannot
drift from the JSON: triggers, the mermaid diagram (from the real connections),
credentials (from node types and auth settings), placeholders, empty resource
pickers and the node list.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import n8nlib as lib  # noqa: E402

STATUS_TEXT = {
    "production pattern": "Production pattern: a template from my agency's client delivery work; per-client copies are made from it.",
    "prototype": "Prototype: built to a prospect's brief to show the approach; not deployed or run against that prospect's accounts.",
}
TABLE_START, TABLE_END = "<!-- library-table:start -->", "<!-- library-table:end -->"
CREDS_START, CREDS_END = "<!-- credentials:start -->", "<!-- credentials:end -->"


def md_escape(text: str) -> str:
    return text.replace("|", "\\|")


def bullet(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items)


def node_list(names: list[str]) -> str:
    return ", ".join(f"`{n}`" for n in names)


def trigger_summary(wf: dict) -> str:
    labels = []
    for n in lib.triggers(wf):
        lab = lib.trigger_label(n)
        if lab in ("Manual (test)", "Error trigger"):
            continue
        if lab not in labels:
            labels.append(lab)
    return " + ".join(labels) or "Manual"


def workflow_readme(entry: dict, wf: dict) -> str:
    nodes = lib.real_nodes(wf)
    stickies = len(wf["nodes"]) - len(nodes)
    creds = lib.credentials_needed(wf)
    ph = lib.placeholders(wf, tuple(entry.get("placeholder_ignore", [])))
    pickers = lib.empty_pickers(wf)
    ai = "yes" if lib.uses_ai(wf) else "no"

    out = [f"# {entry['title']}", ""]
    out.append(f"**Category:** {entry['category']} | **Status:** {entry['status']} | **AI:** {ai} | "
               f"**Nodes:** {len(nodes)}" + (f" plus {stickies} sticky notes" if stickies else ""))
    out += ["", f"> {STATUS_TEXT[entry['status']]}", "", entry["summary"], ""]
    out += ["## What it does", "", entry["description"].strip(), ""]
    out += ["## Trigger", ""]
    out += [f"- `{n['name']}`: {lib.trigger_detail(n)}" for n in lib.triggers(wf)]
    out += ["", "## Flow", "", "```mermaid", lib.mermaid(wf), "```", "",
            "Rounded boxes are triggers, diamonds are decisions, double boxes are AI steps, "
            "dotted lines attach a model, tool or parser to an AI node.", ""]
    out += ["## Key nodes", "", entry["key_nodes"], ""]

    out += ["## What to configure", "", "1. Import `workflow.json` (see [How to import](../../README.md#import-a-workflow)).",
            "2. Create these credentials in n8n and select them in the listed nodes:", ""]
    if creds:
        out += [f"   - **{c}**: {node_list(ns)}" for c, ns in creds.items()]
    else:
        out += ["   - none"]
    step = 3
    if ph:
        out += ["", f"{step}. Replace or fill in these placeholders (some mark an edit spot inside a Code node):", ""]
        out += [f"   - `{tok}` in {node_list(ns)}" for tok, ns in ph.items()]
        step += 1
    if pickers:
        out += ["", f"{step}. Pick these resources in the node (left empty on purpose):", ""]
        out += [f"   - `{n}`: {param}" + (f", shown as \"{hint}\"" if hint else "") for n, param, hint in pickers]
        step += 1
    for extra in entry.get("configure", []):
        out += ["", f"{step}. {extra}"]
        step += 1
    out += ["", "## Design notes", "", bullet(entry["design_notes"]), ""]
    out += ["## Limitations", "", bullet(entry["limitations"]), ""]
    out += [f"<details><summary>All {len(nodes)} nodes</summary>", "", "| Node | Type |", "|---|---|"]
    out += [f"| {md_escape(n['name'])} | `{n['type']}` v{n['typeVersion']} |" for n in nodes]
    out += ["", "</details>", ""]
    return "\n".join(out)


def library_table(catalog: list[dict], wfs: dict[str, dict]) -> str:
    rows = ["| Workflow | Category | Trigger | Key nodes | AI | Status |", "|---|---|---|---|---|---|"]
    for e in catalog:
        wf = wfs[e["slug"]]
        rows.append(
            f"| [{md_escape(e['title'])}](workflows/{e['slug']}/) | {e['category']} | {trigger_summary(wf)} | "
            f"{md_escape(e['key_nodes'])} | {'yes' if lib.uses_ai(wf) else 'no'} | {e['status']} |"
        )
    return "\n".join(rows)


def credentials_table(catalog: list[dict], wfs: dict[str, dict]) -> str:
    by_cred: dict[str, list[str]] = {}
    for e in catalog:
        for cred in lib.credentials_needed(wfs[e["slug"]]):
            label = re.sub(r" for .*$", "", cred) if cred.startswith(("Header Auth", "Basic Auth")) else cred
            target = by_cred.setdefault(label, [])
            if e["slug"] not in target:
                target.append(e["slug"])
    rows = ["| Credential (n8n type) | Used by |", "|---|---|"]
    for cred in sorted(by_cred, key=str.lower):
        links = ", ".join(f"[{s}](workflows/{s}/)" for s in by_cred[cred])
        rows.append(f"| {cred} | {links} |")
    return "\n".join(rows)


def replace_block(text: str, start: str, end: str, body: str) -> str:
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    if not pattern.search(text):
        raise SystemExit(f"README.md is missing the markers {start} ... {end}")
    return pattern.sub(lambda _: f"{start}\n{body}\n{end}", text)


def build() -> dict[Path, str]:
    catalog = lib.load_catalog()
    wfs = {e["slug"]: lib.load_json(lib.WORKFLOWS_DIR / e["slug"] / "workflow.json") for e in catalog}
    files = {lib.WORKFLOWS_DIR / e["slug"] / "README.md": workflow_readme(e, wfs[e["slug"]]) for e in catalog}
    root = lib.ROOT / "README.md"
    text = root.read_text(encoding="utf-8")
    text = replace_block(text, TABLE_START, TABLE_END, library_table(catalog, wfs))
    text = replace_block(text, CREDS_START, CREDS_END, credentials_table(catalog, wfs))
    files[root] = text
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="exit 1 if a generated file is out of date")
    args = ap.parse_args()
    stale = []
    for path, text in build().items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current != text:
            stale.append(path.relative_to(lib.ROOT))
            if not args.check:
                path.write_text(text, encoding="utf-8")
    if args.check:
        if stale:
            print("out of date (run python tools/build_docs.py):", *stale, sep="\n  ")
            return 1
        print("docs up to date")
        return 0
    print(f"wrote {len(stale)} file(s)" + (": " + ", ".join(map(str, stale)) if stale else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
