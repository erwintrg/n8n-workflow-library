#!/usr/bin/env python3
"""Validate the library: every workflow JSON must import cleanly and leak nothing.

    python tools/validate.py                      # workflows/ plus the Markdown docs
    python tools/validate.py --config CONFIG.json # also check the config's blocked_terms
    python tools/validate.py --terms terms.txt    # same, one term per line
    python tools/validate.py path/to/workflow.json ...

Per workflow JSON:
  * parses, has name, nodes, connections; only allowlisted top-level, node and settings keys
    (so no id, versionId, meta, pinData, staticData, tags, node ids, webhookIds or credentials)
  * node names are unique; every connection source and target is an existing node
  * every $('Node') / $node["Node"] / $items("Node") reference points at an existing node
  * no real emails (example.com and reserved TLDs only), phone numbers, Google or YouTube ids,
    UUIDs, secret-looking strings, CLIENTSLUG tokens, em or en dashes
  * every URL host is on the public allowlist below (or is a placeholder)
  * secret-named HTTP parameters hold only expressions or placeholders
  * resource locators for your own resources are empty, expressions or placeholders
Per Markdown file: no em or en dashes, no blocked terms, links to local files resolve.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import n8nlib as lib  # noqa: E402
import sanitize as san  # noqa: E402  (shares its patterns, so the two can never disagree)

# Public API hosts the library is allowed to mention. A new workflow that calls another
# service adds its host here after a review.
ALLOWED_HOSTS = {
    "example.com", "www.example.com", "example.org", "example.net",
    "api.klap.app", "klap.app", "fal.run",
    "generativelanguage.googleapis.com", "docs.google.com", "www.youtube.com", "youtu.be",
    "services.leadconnectorhq.com", "api.hubapi.com", "api.retellai.com", "api.elevenlabs.io",
    "sellingpartnerapi-na.amazon.com", "open-api.tiktokglobalshop.com",
}
URL_RE = re.compile(r"https?://([^/\s\"'`<>()\[\]{}?#:]+)")
DASH_RE = re.compile(f"[{san.EM}{san.EN}]")
SKIP_TEXT_FILES = {"REVIEW.md"}  # private notes for the author, never committed
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules"}


def load_terms(config_path: str | None, terms_path: str | None) -> list[str]:
    terms: list[str] = []
    if config_path:
        terms += lib.load_json(Path(config_path)).get("blocked_terms", [])
    if terms_path:
        terms += [t.strip() for t in Path(terms_path).read_text(encoding="utf-8").splitlines()
                  if t.strip() and not t.startswith("#")]
    return sorted({t for t in terms if t}, key=str.lower)


def term_regex(terms: list[str]) -> re.Pattern | None:
    if not terms:
        return None
    return re.compile(r"(?<![\w])(" + "|".join(re.escape(t) for t in terms) + r")(?![\w])", re.I)


def _string_findings(text: str, where: str, blocked: re.Pattern | None) -> list[str]:
    errs = []
    for m in san.EMAIL_RE.finditer(text):
        d = m.group(2).lower()
        if not (d in san.ALLOWED_EMAIL_DOMAINS or any(d.endswith("." + a) for a in san.ALLOWED_EMAIL_DOMAINS)
                or d.endswith(san.RESERVED_TLDS)):
            errs.append(f"{where}: real-looking email {m.group(0)!r}")
    checks = [
        (san.PHONE_RE, "phone number"), (san.GOOGLE_ID_RE, "Google id"),
        (san.YT_CHANNEL_RE, "YouTube channel id"), (re.compile(san.UUID, re.I), "UUID"),
    ] + [(rx, "secret-looking string") for rx, _ in san.SECRET_RES]
    for rx, what in checks:
        for m in rx.finditer(text):
            if what == "secret-looking string" and m.group(0).endswith("YOUR_API_KEY"):
                continue
            errs.append(f"{where}: {what} {m.group(0)[:24]!r}")
    for rx, _ in san.GOOGLE_URL_RES:
        for m in rx.finditer(text):
            errs.append(f"{where}: Google URL with a real id")
    if san.KEYVAL_SECRET_RE.search(text):
        errs.append(f"{where}: key/value secret")
    for m in URL_RE.finditer(text):
        host = m.group(1).lower()
        if host not in ALLOWED_HOSTS and not host.startswith(("your", "n8n_base_url")) and "{{" not in host:
            errs.append(f"{where}: URL host not on the allowlist: {host}")
    if "CLIENTSLUG" in text:
        errs.append(f"{where}: CLIENTSLUG token (should be client-slug)")
    if DASH_RE.search(text):
        errs.append(f"{where}: em or en dash")
    if blocked:
        for m in blocked.finditer(text):
            errs.append(f"{where}: blocked term {m.group(0)!r}")
    return errs


def validate_workflow(wf, rel: str, blocked: re.Pattern | None = None) -> list[str]:
    errs: list[str] = []
    if not isinstance(wf, dict):
        return [f"{rel}: top level is not an object"]
    for k in wf:
        if k not in lib.ALLOWED_TOP_KEYS:
            errs.append(f"{rel}: top-level key not allowed: {k}")
    if not isinstance(wf.get("name"), str) or not wf.get("name"):
        errs.append(f"{rel}: missing name")
    nodes, conns = wf.get("nodes"), wf.get("connections")
    if not isinstance(nodes, list) or not nodes:
        return errs + [f"{rel}: nodes must be a non-empty list"]
    if not isinstance(conns, dict):
        return errs + [f"{rel}: connections must be an object"]
    for k in (wf.get("settings") or {}):
        if k not in lib.ALLOWED_SETTINGS:
            errs.append(f"{rel}: settings key not allowed: {k}")

    names: list[str] = []
    for i, n in enumerate(nodes):
        where = f"{rel}: node[{i}]"
        if not isinstance(n, dict):
            errs.append(f"{where} is not an object")
            continue
        for k in n:
            if k not in lib.ALLOWED_NODE_KEYS:
                errs.append(f"{where} {n.get('name')!r}: key not allowed: {k}")
        if not isinstance(n.get("name"), str) or not n["name"]:
            errs.append(f"{where}: missing name")
            continue
        names.append(n["name"])
        if not isinstance(n.get("type"), str) or "." not in n["type"]:
            errs.append(f"{where} {n['name']!r}: bad type")
        if not isinstance(n.get("typeVersion"), (int, float)):
            errs.append(f"{where} {n['name']!r}: bad typeVersion")
        pos = n.get("position")
        if not (isinstance(pos, list) and len(pos) == 2 and all(isinstance(v, (int, float)) for v in pos)):
            errs.append(f"{where} {n['name']!r}: bad position")
        if not isinstance(n.get("parameters"), dict):
            errs.append(f"{where} {n['name']!r}: parameters must be an object")
            continue
        errs += _node_param_checks(n, f"{rel}: {n['name']!r}")
    dupes = sorted({x for x in names if names.count(x) > 1})
    if dupes:
        errs.append(f"{rel}: duplicate node names {dupes}")
    known = set(names)

    for src, outs in conns.items():
        if src not in known:
            errs.append(f"{rel}: connection from unknown node {src!r}")
        if not isinstance(outs, dict):
            errs.append(f"{rel}: connections[{src!r}] is not an object")
            continue
        for kind, lists in outs.items():
            if not isinstance(lists, list):
                errs.append(f"{rel}: connections[{src!r}][{kind!r}] is not a list")
                continue
            for out_idx, targets in enumerate(lists):
                for t in targets or []:
                    if not isinstance(t, dict) or t.get("node") not in known:
                        errs.append(f"{rel}: {src!r} {kind}[{out_idx}] points at unknown node {t!r}")
                    elif not isinstance(t.get("type"), str) or not isinstance(t.get("index"), int) or t["index"] < 0:
                        errs.append(f"{rel}: {src!r} {kind}[{out_idx}] has a bad target {t!r}")

    for path, text in lib.iter_strings(wf):
        for ref in lib.node_refs(text):
            if ref not in known:
                errs.append(f"{rel}: {'/'.join(map(str, path[:3]))} references unknown node {ref!r}")
        errs += _string_findings(text, f"{rel}: {'/'.join(map(str, path[:4]))}", blocked)
    return errs


def _node_param_checks(node: dict, where: str) -> list[str]:
    errs = []

    def walk(obj, parent_key=None):
        if isinstance(obj, dict):
            if obj.get("__rl") is True:
                ph = "YOUR_" if parent_key == "documentId" else san.RL_PLACEHOLDERS.get(parent_key or "")
                if ph and san.literal_resource_id(obj):
                    errs.append(f"{where}: {parent_key} holds a literal id, use a YOUR_ placeholder")
                if obj.get("cachedResultUrl"):
                    errs.append(f"{where}: {parent_key} keeps a cachedResultUrl")
            for k, v in obj.items():
                if k in ("headerParameters", "queryParameters", "bodyParameters") and isinstance(v, dict):
                    for prm in v.get("parameters") or []:
                        val = prm.get("value")
                        if isinstance(val, str) and val and san.SECRET_PARAM_NAME_RE.search(str(prm.get("name", ""))) \
                                and not san.is_expression(val) and not san.is_placeholder(val):
                            errs.append(f"{where}: {k} {prm.get('name')!r} holds a literal secret")
                walk(v, k)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, parent_key)

    walk(node.get("parameters", {}))
    if node.get("type") in san.PATH_NODE_TYPES:
        p = node.get("parameters", {})
        for path in (p.get("path"), (p.get("options") or {}).get("path") if isinstance(p.get("options"), dict) else None):
            if isinstance(path, str) and san.UUID_RE.match(path):
                errs.append(f"{where}: webhook path is a UUID")
    return errs


def validate_markdown(path: Path, root: Path, blocked: re.Pattern | None) -> list[str]:
    rel = str(path.relative_to(root))
    text = path.read_text(encoding="utf-8")
    errs = []
    for i, line in enumerate(text.splitlines(), 1):
        if DASH_RE.search(line):
            errs.append(f"{rel}:{i}: em or en dash")
        if blocked:
            for m in blocked.finditer(line):
                errs.append(f"{rel}:{i}: blocked term {m.group(0)!r}")
    for m in re.finditer(r"\]\((?!https?://|#|mailto:)([^)\s]+)\)", text):
        target = (path.parent / m.group(1).split("#")[0]).resolve()
        if not target.exists():
            errs.append(f"{rel}: broken link {m.group(1)}")
    return errs


def scan_text_files(root: Path, blocked: re.Pattern) -> list[str]:
    """Blocked terms anywhere in the repo text (code, fixtures, config), not just workflows."""
    errs = []
    for p in sorted(root.rglob("*")):
        if p.is_dir() or any(part in SKIP_DIRS for part in p.parts) or p.name in SKIP_TEXT_FILES:
            continue
        if p.suffix.lower() not in {".py", ".md", ".json", ".toml", ".txt", ".example", ".cfg", ".ini", ""}:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for m in blocked.finditer(line):
                errs.append(f"{p.relative_to(root)}:{i}: blocked term {m.group(0)!r}")
    return errs


def validate_tree(workflows_dir: Path, terms: list[str] | None = None) -> list[str]:
    blocked = term_regex(terms or [])
    errs = []
    files = lib.workflow_files(workflows_dir)
    if not files:
        errs.append(f"no workflow JSON found under {workflows_dir}")
    for f in files:
        try:
            wf = lib.load_json(f)
        except ValueError as e:
            errs.append(f"{f}: does not parse: {e}")
            continue
        errs += validate_workflow(wf, str(f.relative_to(workflows_dir.parent)), blocked)
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="workflow JSON files (default: everything under workflows/)")
    ap.add_argument("--config", help="sanitize config whose blocked_terms are checked too")
    ap.add_argument("--terms", help="file with extra blocked terms, one per line")
    args = ap.parse_args()

    terms = load_terms(args.config, args.terms)
    blocked = term_regex(terms)
    root = lib.ROOT
    errors: list[str] = []
    if args.paths:
        files = [Path(p) for p in args.paths]
        for f in files:
            errors += validate_workflow(lib.load_json(f), str(f), blocked)
    else:
        files = lib.workflow_files()
        errors += validate_tree(lib.WORKFLOWS_DIR, terms)
        md = [p for p in sorted(root.rglob("*.md"))
              if p.name not in SKIP_TEXT_FILES and not any(part in SKIP_DIRS for part in p.parts)]
        for p in md:
            errors += validate_markdown(p, root, blocked)
        if blocked:
            errors += scan_text_files(root, blocked)
        print(f"checked {len(files)} workflow files, {len(md)} Markdown files"
              + (f", {len(terms)} blocked terms" if terms else ""))
    for e in errors:
        print("FAIL", e)
    if errors:
        print(f"{len(errors)} problem(s)")
        return 1
    for f in files:
        wf = lib.load_json(f)
        shown = f.relative_to(root) if f.is_absolute() else f
        print(f"ok   {shown}  ({len(lib.real_nodes(wf))} nodes, "
              f"{len(lib.credentials_needed(wf))} credential types, "
              f"{sum(len(t or []) for o in wf['connections'].values() for ts in o.values() for t in ts)} connections)")
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
