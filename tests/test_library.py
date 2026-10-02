"""The published library: every workflow valid, docs fresh, diagrams complete."""
import re

import build_docs
import n8nlib as lib
import validate as val


def test_library_passes_validation():
    assert val.validate_tree(lib.WORKFLOWS_DIR) == []


def test_catalog_and_folders_match():
    slugs = {e["slug"] for e in lib.load_catalog()}
    folders = {p.parent.name for p in lib.workflow_files()}
    assert slugs == folders
    for e in lib.load_catalog():
        assert e["status"] in build_docs.STATUS_TEXT
        assert lib.load_json(lib.WORKFLOWS_DIR / e["slug"] / "workflow.json")["name"] == e["title"]


def test_generated_docs_are_up_to_date():
    for path, text in build_docs.build().items():
        assert path.read_text(encoding="utf-8") == text, f"run python tools/build_docs.py ({path.name})"


def test_markdown_copy_rules():
    for md in sorted(lib.ROOT.rglob("*.md")):
        if md.name in val.SKIP_TEXT_FILES or any(p in val.SKIP_DIRS for p in md.parts):
            continue
        assert val.validate_markdown(md, lib.ROOT, None) == [], md


def test_every_connection_is_in_the_diagram():
    for f in lib.workflow_files():
        wf = lib.load_json(f)
        edges = len(re.findall(r"-->|-\.->", lib.mermaid(wf)))
        real = {n["name"] for n in lib.real_nodes(wf)}
        expected = sum(1 for src, outs in wf["connections"].items() if src in real
                       for lists in outs.values() for targets in lists for t in (targets or []) if t["node"] in real)
        assert edges == expected, f.parent.name


def test_validator_catches_broken_wiring():
    wf = {"name": "x", "settings": {}, "nodes": [
        {"name": "A", "type": "n8n-nodes-base.set", "typeVersion": 3.4, "position": [0, 0],
         "parameters": {"value": "={{ $('Missing').item.json.x }}"}}],
        "connections": {"A": {"main": [[{"node": "Ghost", "type": "main", "index": 0}]]},
                        "Nobody": {"main": [[{"node": "A", "type": "main", "index": 0}]]}}}
    problems = "\n".join(val.validate_workflow(wf, "x"))
    assert "unknown node 'Missing'" in problems
    assert "'Ghost'" in problems
    assert "connection from unknown node 'Nobody'" in problems


def test_validator_catches_leaks():
    secret = "xox" + "b-" + "1" * 12 + "-abcdefghij"
    wf = {"name": "x", "nodes": [
        {"name": "A", "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [0, 0], "id": "abc",
         "parameters": {"jsCode": f"// mail ann@company.io, token {secret}, see https://internal.company.io/x"}}],
        "connections": {}}
    problems = "\n".join(val.validate_workflow(wf, "x", val.term_regex(["Ann"])))
    for expected in ("key not allowed: id", "real-looking email", "secret-looking string",
                     "URL host not on the allowlist: internal.company.io", "blocked term 'ann'"):
        assert expected in problems, expected


def test_credentials_are_documented_for_every_credentialed_node_type():
    seen = set()
    for f in lib.workflow_files():
        for creds in lib.credentials_needed(lib.load_json(f)):
            seen.add(creds)
    assert "Gmail OAuth2" in seen and "OpenAI API" in seen
    assert all(c.strip() for c in seen)
