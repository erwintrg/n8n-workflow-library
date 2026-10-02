#!/usr/bin/env python3
"""Offline demo: no account, no key, no network.

1. Validates every workflow in the library and prints one summary line each.
2. Sanitizes a fake raw n8n export (tests/fixtures/raw/) and shows what changed.
3. Prints the mermaid diagram generated from one workflow's connections.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))

import n8nlib as lib  # noqa: E402
import sanitize as san  # noqa: E402
import validate as val  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "raw" / "demo-lead-router.json"
EXAMPLE_CONFIG = ROOT / "sanitize.config.example.json"
DIAGRAM_SLUG = "typeform-to-hubspot"


def short(text: str, width: int = 74) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[: width - 3] + "..."


def library_summary() -> int:
    print("1) Library check\n")
    errors = val.validate_tree(lib.WORKFLOWS_DIR)
    catalog = {e["slug"]: e for e in lib.load_catalog()}
    print(f"   {'workflow':<38} {'nodes':>5}  {'AI':<3} {'creds':>5} {'to fill':>7}  trigger")
    total = 0
    for f in lib.workflow_files():
        wf = lib.load_json(f)
        slug = f.parent.name
        entry = catalog.get(slug, {})
        n = len(lib.real_nodes(wf))
        total += n
        trig = " + ".join(dict.fromkeys(lib.trigger_label(t) for t in lib.triggers(wf)))
        ph = lib.placeholders(wf, tuple(entry.get("placeholder_ignore", [])))
        print(f"   {slug:<38} {n:>5}  {'yes' if lib.uses_ai(wf) else 'no':<3} "
              f"{len(lib.credentials_needed(wf)):>5} {len(ph) + len(lib.empty_pickers(wf)):>7}  {trig}")
    print(f"\n   {len(lib.workflow_files())} workflows, {total} nodes, "
          + ("all checks passed" if not errors else f"{len(errors)} problem(s):"))
    for e in errors:
        print("   FAIL", e)
    return len(errors)


def sanitizer_demo() -> int:
    print("\n2) Sanitizer on a fake raw export\n")
    raw = lib.load_json(FIXTURE)
    config = lib.load_json(EXAMPLE_CONFIG)
    s = san.Sanitizer(config)
    clean = s.workflow(raw, slug="demo-lead-router", name="Demo lead router")

    raw_problems = val.validate_workflow(raw, "raw export")
    clean_problems = val.validate_workflow(clean, "sanitized")
    creds = sum(1 for n in raw["nodes"] if "credentials" in n)
    hooks = sum(1 for n in raw["nodes"] if "webhookId" in n)
    print(f"   input:  {FIXTURE.relative_to(ROOT)}")
    print(f"   before: {len(raw)} top-level keys, {len(raw['nodes'])} node ids, {hooks} webhook ids, "
          f"{creds} credential blocks")
    print(f"   after:  {len(clean)} top-level keys ({', '.join(clean)}), no ids, no credentials")
    print("   rules applied: " + ", ".join(f"{k} {v}" for k, v in sorted(s.stats.items()) if v))

    print("\n   changed values (before -> after):")
    for rn, cn in zip(raw["nodes"], clean["nodes"]):
        if rn["name"] != cn["name"]:
            print(f"   - node name: {rn['name']!r}\n          -> {cn['name']!r}")
        before = dict(lib.iter_strings(rn.get("parameters", {}), include_keys=False))
        for p, after in lib.iter_strings(cn.get("parameters", {}), include_keys=False):
            b = before.get(p)
            if b is None or b == after:
                continue
            line_b = next((ln for ln in b.splitlines() if ln not in after.splitlines()), b)
            line_a = next((ln for ln in after.splitlines() if ln not in b.splitlines()), after)
            print(f"   - {cn['name']} / {p[-1]}:\n          {short(line_b)}\n       -> {short(line_a)}")

    print(f"\n   validator on the raw export: {len(raw_problems)} problems, for example:")
    for e in raw_problems[:6]:
        print(f"     {short(e, 96)}")
    print(f"   validator on the sanitized result: {len(clean_problems)} problems")
    return len(clean_problems)


def diagram() -> None:
    wf = lib.load_json(lib.WORKFLOWS_DIR / DIAGRAM_SLUG / "workflow.json")
    print(f"\n3) Diagram generated from the connections of {DIAGRAM_SLUG}\n")
    print("\n".join("   " + ln for ln in lib.mermaid(wf).splitlines()))
    print("\n   Paste it into any mermaid viewer, or open the workflow README on GitHub.")


def main() -> int:
    failures = library_summary()
    failures += sanitizer_demo()
    diagram()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
