#!/usr/bin/env python3
"""Turn raw n8n workflow exports into sanitized, importable library files.

Deterministic: the same raw export and config always give byte-identical output,
so the script can be re-run after every fetch.

    python tools/sanitize.py --config CONFIG.json --raw RAW_DIR [--out workflows] [--env-file .env] [--check]

CONFIG is JSON. Keep your real one outside the repository, because it names the
things you are removing. See sanitize.config.example.json for the shape:

    workflows      [{"source": "<raw file stem>", "slug": "<folder name>"}]
    instance_urls  origins of your n8n instance, replaced with N8N_BASE_URL
    replace        ordered [old, new] pairs, applied first, case-sensitive
    emails         optional {"real@address": "placeholder@example.com"}
    blocked_terms  terms validate.py must never find afterwards

The workflow name in the output comes from catalog.toml (by slug). The origin in
N8N_BASE_URL (environment or --env-file) is added to instance_urls automatically.
Nothing is printed from the .env file.

What it does to every workflow, in order:
 1. keeps only name, nodes, connections and an allowlist of settings
    (drops id, versionId, meta, pinData, staticData, tags, sharing and history)
 2. keeps only an allowlist of node keys (drops node id, webhookId, credentials)
 3. resource locators: real Sheet, Doc, Drive, workflow and channel ids become
    YOUR_SHEET_ID style placeholders, cached URLs are dropped
 4. HTTP header, query and body parameters with secret names get YOUR_API_KEY
 5. webhook and form paths that are UUIDs become readable slugs
 6. text rules on every string, dict keys included (so connections and $('Node')
    references stay consistent): config replacements, instance URL, CLIENTSLUG,
    emails, phone numbers, Google ids, YouTube channel ids, secret patterns,
    em and en dashes
"""
from __future__ import annotations

import argparse
import copy
import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import n8nlib as lib  # noqa: E402

EM, EN = "—", "–"

ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")
RESERVED_TLDS = (".example", ".test", ".invalid", ".localhost")
ROLE_LOCALS = {
    "info", "sales", "ops", "support", "test", "shorts", "contact", "hello", "admin", "manager", "team",
    "client", "billing", "accounts", "noreply", "no-reply", "office", "you", "someone", "user", "hr", "jobs",
}

EMAIL_RE = re.compile(r"(?<![\w.%+-])([A-Za-z0-9._%+-]+)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})(?![\w-])")
PHONE_RE = re.compile(r"(?<![\w+])\+\d{1,3}(?:[ .()/-]*\d){7,13}(?!\d)")
GOOGLE_URL_RES = (
    (re.compile(r"(https?://docs\.google\.com/spreadsheets/d/)([A-Za-z0-9_-]{20,})"), "YOUR_SHEET_ID"),
    (re.compile(r"(https?://docs\.google\.com/document/d/)([A-Za-z0-9_-]{20,})"), "YOUR_DOC_ID"),
    (re.compile(r"(https?://docs\.google\.com/presentation/d/)([A-Za-z0-9_-]{20,})"), "YOUR_SLIDES_ID"),
    (re.compile(r"(https?://docs\.google\.com/forms/d/)([A-Za-z0-9_-]{20,})"), "YOUR_FORM_ID"),
    (re.compile(r"(https?://drive\.google\.com/drive/(?:u/\d+/)?folders/)([A-Za-z0-9_-]{20,})"), "YOUR_FOLDER_ID"),
    (re.compile(r"(https?://drive\.google\.com/file/d/)([A-Za-z0-9_-]{20,})"), "YOUR_FILE_ID"),
    (re.compile(r"(https?://drive\.google\.com/open\?id=)([A-Za-z0-9_-]{20,})"), "YOUR_FILE_ID"),
)
GOOGLE_ID_RE = re.compile(r"(?<![\w-])1[A-Za-z0-9_-]{32,43}(?![\w-])")
YT_CHANNEL_RE = re.compile(r"(?<![\w-])UC[A-Za-z0-9_-]{22}(?![\w-])")
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
UUID_RE = re.compile(rf"^{UUID}$")
UUID_URL_PATH_RE = re.compile(rf"/(webhook|webhook-test|webhook-waiting|form|form-test)/{UUID}")
SECRET_RES = (
    (re.compile(r"\bsk-(?:proj-|ant-|live_|test_)?[A-Za-z0-9_-]{20,}"), "YOUR_API_KEY"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"), "YOUR_TOKEN"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), "YOUR_API_KEY"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "YOUR_SLACK_TOKEN"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), "YOUR_GITHUB_TOKEN"),
    (re.compile(r"\bpit-[0-9a-f]{8}-[0-9a-f-]{20,}"), "YOUR_API_KEY"),
    (re.compile(r"\bwhsec_[A-Za-z0-9]{20,}"), "YOUR_WEBHOOK_SECRET"),
    (re.compile(r"\bre_[A-Za-z0-9]{8,}_[A-Za-z0-9]{8,}"), "YOUR_API_KEY"),
    (re.compile(r"(?i)\bBearer\s+(?!YOUR_|\[|\{\{|=|\$)[A-Za-z0-9._~+/=-]{16,}"), "Bearer YOUR_API_KEY"),
)
KEYVAL_SECRET_RE = re.compile(
    r"""(?i)((?:api[_-]?key|apikey|secret|token|password|passwd)["']?\s*[:=]\s*["'])"""
    r"""(?!YOUR_|\[|\{\{|=|\$)([A-Za-z0-9_\-.+/=]{16,})(["'])"""
)
SECRET_PARAM_NAME_RE = re.compile(
    r"(?i)(authori[sz]ation|api[-_ ]?key|apikey|token|secret|password|passwd|access[-_]?key|signature|^sign$|cookie|session)"
)

# Resource locator keys that hold ids of your own resources, and their placeholders.
RL_PLACEHOLDERS = {
    "folderId": "YOUR_FOLDER_ID", "fileId": "YOUR_FILE_ID", "driveId": "YOUR_DRIVE_ID",
    "workflowId": "YOUR_WORKFLOW_ID", "calendar": "YOUR_CALENDAR_ID", "calendarId": "YOUR_CALENDAR_ID",
    "channelId": "YOUR_CHANNEL_ID", "workbook": "YOUR_WORKBOOK_ID", "worksheet": "YOUR_WORKSHEET_ID",
    "site": "YOUR_SITE_ID", "list": "YOUR_LIST_ID", "base": "YOUR_BASE_ID", "table": "YOUR_TABLE_ID",
    "databaseId": "YOUR_DATABASE_ID", "pageId": "YOUR_PAGE_ID", "playlistId": "YOUR_PLAYLIST_ID",
    "spreadsheetId": "YOUR_SHEET_ID", "sheetId": "YOUR_SHEET_ID", "boardId": "YOUR_BOARD_ID",
}
DOC_ID_BY_TYPE = {
    "n8n-nodes-base.googleSheets": "YOUR_SHEET_ID", "n8n-nodes-base.googleSheetsTrigger": "YOUR_SHEET_ID",
    "n8n-nodes-base.googleDocs": "YOUR_DOC_ID", "n8n-nodes-base.googleSlides": "YOUR_SLIDES_ID",
}
RL_ID_VALUE_RE = re.compile(r"^[A-Za-z0-9_-]{9,}$")
PATH_NODE_TYPES = {"n8n-nodes-base.webhook", "n8n-nodes-base.formTrigger", "@n8n/n8n-nodes-langchain.mcpTrigger"}


def is_expression(value: str) -> bool:
    return value.startswith("=") or "{{" in value


def is_placeholder(value: str) -> bool:
    return ("[" in value and "]" in value) or "YOUR_" in value or "REPLACE_WITH_" in value


def literal_resource_id(locator: dict) -> bool:
    """True when a resource locator holds a real-looking id (not a name, expression or placeholder)."""
    val = locator.get("value")
    return (isinstance(val, str) and bool(val) and not is_expression(val) and not is_placeholder(val)
            and bool(RL_ID_VALUE_RE.match(val) or locator.get("mode") == "url"))


class Sanitizer:
    def __init__(self, config: dict, instance_urls: list[str] | None = None):
        self.replace = [tuple(p) for p in config.get("replace", [])]
        self.emails = {k.lower(): v for k, v in (config.get("emails") or {}).items()}
        origins = list(config.get("instance_urls", [])) + list(instance_urls or [])
        self.origins = sorted({o.rstrip("/") for o in origins if o}, key=len, reverse=True)
        self.hosts = sorted({re.sub(r"^https?://", "", o).split("/")[0] for o in self.origins}, key=len, reverse=True)
        self.stats: Counter = Counter()

    # ------------------------------------------------------------- text rules
    def text(self, s: str) -> str:
        for old, new in self.replace:
            if old in s:
                self.stats["config replacements"] += s.count(old)
                s = s.replace(old, new)
        for origin in self.origins:
            s, n = re.subn(re.escape(origin), "N8N_BASE_URL", s, flags=re.I)
            self.stats["instance URL"] += n
        for host in self.hosts:
            s, n = re.subn(rf"(?<![\w.-]){re.escape(host)}(?![\w-])", "N8N_BASE_URL", s, flags=re.I)
            self.stats["instance URL"] += n
        s, n = UUID_URL_PATH_RE.subn(lambda m: f"/{m.group(1)}/your-webhook-path", s)
        self.stats["webhook ids in URLs"] += n
        if "CLIENTSLUG" in s:
            self.stats["CLIENTSLUG"] += s.count("CLIENTSLUG")
            s = s.replace("CLIENTSLUG", "client-slug")
        s = EMAIL_RE.sub(self._email, s)
        s, n = PHONE_RE.subn("YOUR_PHONE_NUMBER", s)
        self.stats["phone numbers"] += n
        for rx, ph in GOOGLE_URL_RES:
            s, n = rx.subn(lambda m, ph=ph: m.group(1) + ph, s)
            self.stats["Google ids"] += n
        s, n = GOOGLE_ID_RE.subn("YOUR_GOOGLE_ID", s)
        self.stats["Google ids"] += n
        s, n = YT_CHANNEL_RE.subn("YOUR_CHANNEL_ID", s)
        self.stats["YouTube channel ids"] += n
        for rx, ph in SECRET_RES:
            s, n = rx.subn(ph, s)
            self.stats["secrets"] += n
        s, n = KEYVAL_SECRET_RE.subn(lambda m: m.group(1) + "YOUR_API_KEY" + m.group(3), s)
        self.stats["secrets"] += n
        if EM in s or EN in s:
            self.stats["em/en dashes"] += s.count(EM) + s.count(EN)
            for d in (EM, EN):
                s = s.replace(f" {d} ", " - ").replace(d, "-")
        return s

    def _email(self, m: re.Match) -> str:
        local, domain = m.group(1), m.group(2).lower()
        full = f"{local}@{domain}".lower()
        if full in self.emails:
            self.stats["emails"] += 1
            return self.emails[full]
        if domain in ALLOWED_EMAIL_DOMAINS or any(domain.endswith("." + d) for d in ALLOWED_EMAIL_DOMAINS) \
                or domain.endswith(RESERVED_TLDS):
            return m.group(0)
        self.stats["emails"] += 1
        return (local.lower() if local.lower() in ROLE_LOCALS else "someone") + "@example.com"

    # ------------------------------------------------------- structural rules
    def _locators(self, obj, node_type: str, parent_key: str | None = None) -> None:
        if isinstance(obj, dict):
            if obj.get("__rl") is True:
                if obj.pop("cachedResultUrl", None) not in (None, ""):
                    self.stats["cached URLs"] += 1
                if parent_key == "documentId":
                    ph = DOC_ID_BY_TYPE.get(node_type, "YOUR_DOC_ID")
                else:
                    ph = RL_PLACEHOLDERS.get(parent_key or "")
                if ph and literal_resource_id(obj):
                    obj["value"], obj["mode"] = ph, "id"
                    obj.pop("cachedResultName", None)
                    self.stats["resource ids"] += 1
            for key, val in obj.items():
                if key in ("headerParameters", "queryParameters", "bodyParameters") and isinstance(val, dict):
                    for prm in val.get("parameters") or []:
                        self._secret_param(prm)
                self._locators(val, node_type, key)
        elif isinstance(obj, list):
            for val in obj:
                self._locators(val, node_type, parent_key)

    def _secret_param(self, prm: dict) -> None:
        name, val = str(prm.get("name", "")), prm.get("value")
        if isinstance(val, str) and val and SECRET_PARAM_NAME_RE.search(name) \
                and not is_expression(val) and not is_placeholder(val):
            prm["value"] = "Bearer YOUR_API_KEY" if val.lower().startswith("bearer ") else "YOUR_API_KEY"
            self.stats["secret header/query values"] += 1

    def _paths(self, node: dict, slug: str, used: set[str]) -> None:
        if node.get("type") not in PATH_NODE_TYPES:
            return
        params = node.get("parameters", {})
        for holder in (params, params.get("options") if isinstance(params.get("options"), dict) else None):
            if holder and isinstance(holder.get("path"), str) and UUID_RE.match(holder["path"]):
                new, i = slug, 2
                while new in used:
                    new, i = f"{slug}-{i}", i + 1
                used.add(new)
                holder["path"] = new
                self.stats["UUID webhook paths"] += 1

    # ------------------------------------------------------------- workflow
    def workflow(self, raw: dict, *, slug: str, name: str | None = None) -> dict:
        dropped_top = [k for k in raw if k not in lib.ALLOWED_TOP_KEYS]
        self.stats["top-level keys dropped"] += len(dropped_top)
        nodes = []
        used_paths: set[str] = set()
        for node in raw.get("nodes", []):
            for k in node:
                if k not in lib.ALLOWED_NODE_KEYS:
                    self.stats[f"node '{k}' dropped"] += 1
            clean = {k: copy.deepcopy(node[k]) for k in lib.ALLOWED_NODE_KEYS if k in node}
            self._locators(clean.get("parameters", {}), clean.get("type", ""))
            self._paths(clean, slug, used_paths)
            nodes.append(clean)
        settings = {k: v for k, v in (raw.get("settings") or {}).items() if k in lib.ALLOWED_SETTINGS}
        self.stats["settings dropped"] += len(raw.get("settings") or {}) - len(settings)
        out = {
            "name": name if name else raw.get("name", slug),
            "nodes": nodes,
            "connections": copy.deepcopy(raw.get("connections") or {}),
            "settings": settings,
        }
        out = lib.map_strings(out, self.text)
        names = [n["name"] for n in out["nodes"]]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"{slug}: node names collide after sanitizing: {dupes}")
        return out


def instance_urls_from_env(env_file: str | None) -> list[str]:
    base = os.environ.get("N8N_BASE_URL", "")
    if env_file and Path(env_file).is_file():
        try:
            from dotenv import dotenv_values
        except ImportError:
            sys.exit("python-dotenv is needed for --env-file (pip install python-dotenv)")
        base = dotenv_values(env_file).get("N8N_BASE_URL") or base
    m = re.match(r"(https?://[^/]+)", base or "")
    return [m.group(1)] if m else []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, help="sanitize config JSON (keep it outside the repo)")
    ap.add_argument("--raw", required=True, help="folder with raw exports from tools/fetch.py")
    ap.add_argument("--out", default=str(lib.WORKFLOWS_DIR), help="library folder (default: workflows/)")
    ap.add_argument("--env-file", help=".env with N8N_BASE_URL, so your instance URL is always replaced")
    ap.add_argument("--check", action="store_true", help="write nothing, fail if any output would change")
    args = ap.parse_args()

    config = lib.load_json(Path(args.config))
    titles = {w["slug"]: w["title"] for w in lib.load_catalog()} if lib.CATALOG_PATH.exists() else {}
    out_dir, raw_dir = Path(args.out), Path(args.raw)
    instance = instance_urls_from_env(args.env_file)
    changed = []
    for entry in config["workflows"]:
        slug = entry["slug"]
        raw = lib.load_json(raw_dir / f"{entry['source']}.json")
        s = Sanitizer(config, instance)
        wf = s.workflow(raw, slug=slug, name=titles.get(slug) or entry.get("name"))
        text = lib.dump_json(wf)
        target = out_dir / slug / "workflow.json"
        if not target.exists() or target.read_text(encoding="utf-8") != text:
            changed.append(slug)
            if not args.check:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
        stats = ", ".join(f"{k} {v}" for k, v in sorted(s.stats.items()) if v)
        print(f"{slug}: {len(wf['nodes'])} nodes | {stats}")

    import validate  # same folder

    terms = validate.load_terms(args.config, None)
    errors = validate.validate_tree(out_dir, terms)
    for e in errors:
        print("VALIDATE:", e)
    if args.check:
        print("unchanged" if not changed else f"would change: {', '.join(changed)}")
        return 1 if changed or errors else 0
    print(f"wrote {len(changed)} changed file(s), {len(config['workflows'])} workflow(s) total")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
