"""Shared helpers for the library tools: loading, walking and describing n8n workflows.

Standard library only, so validate, docs and the demo run on a bare Python 3.11+.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = ROOT / "workflows"
CATALOG_PATH = ROOT / "catalog.toml"

# What a sanitized workflow may contain. Everything else is dropped by sanitize.py
# and rejected by validate.py.
ALLOWED_TOP_KEYS = ("name", "nodes", "connections", "settings")
ALLOWED_NODE_KEYS = (
    "name", "type", "typeVersion", "position", "parameters", "disabled", "notes", "notesInFlow",
    "retryOnFail", "maxTries", "waitBetweenTries", "alwaysOutputData", "executeOnce", "onError",
    "continueOnFail",
)
ALLOWED_SETTINGS = (
    "executionOrder", "binaryMode", "executionTimeout", "saveExecutionProgress", "saveManualExecutions",
    "saveDataErrorExecution", "saveDataSuccessExecution", "timezone", "callerPolicy",
)

STICKY = "n8n-nodes-base.stickyNote"

TRIGGER_LABELS = {
    "n8n-nodes-base.webhook": "Webhook",
    "n8n-nodes-base.formTrigger": "Form",
    "n8n-nodes-base.gmailTrigger": "Gmail",
    "n8n-nodes-base.scheduleTrigger": "Schedule",
    "n8n-nodes-base.manualTrigger": "Manual (test)",
    "n8n-nodes-base.errorTrigger": "Error trigger",
    "n8n-nodes-base.typeformTrigger": "Typeform",
    "n8n-nodes-base.googleDriveTrigger": "Google Drive",
}

# Node type -> credential the node needs in n8n. HTTP Request nodes are handled separately.
CREDENTIALS_BY_TYPE = {
    "n8n-nodes-base.gmail": "Gmail OAuth2",
    "n8n-nodes-base.gmailTrigger": "Gmail OAuth2",
    "n8n-nodes-base.googleSheets": "Google Sheets OAuth2",
    "n8n-nodes-base.googleDocs": "Google Docs OAuth2",
    "n8n-nodes-base.googleDrive": "Google Drive OAuth2",
    "n8n-nodes-base.googleDriveTrigger": "Google Drive OAuth2",
    "n8n-nodes-base.youTube": "YouTube OAuth2",
    "n8n-nodes-base.slack": "Slack",
    "n8n-nodes-base.telegram": "Telegram API",
    "n8n-nodes-base.postgres": "Postgres",
    "n8n-nodes-base.pipedrive": "Pipedrive API",
    "n8n-nodes-base.hubspot": "HubSpot App Token",
    "n8n-nodes-base.typeformTrigger": "Typeform API",
    "n8n-nodes-base.microsoftSharePoint": "Microsoft SharePoint OAuth2",
    "n8n-nodes-base.microsoftOutlook": "Microsoft Outlook OAuth2",
    "n8n-nodes-base.microsoftExcel": "Microsoft Excel 365 OAuth2",
    "@n8n/n8n-nodes-langchain.openAi": "OpenAI API",
    "@n8n/n8n-nodes-langchain.lmChatOpenAi": "OpenAI API",
    "@n8n/n8n-nodes-langchain.embeddingsOpenAi": "OpenAI API",
    "@n8n/n8n-nodes-langchain.lmChatGoogleGemini": "Google Gemini (PaLM) API",
    "@n8n/n8n-nodes-langchain.lmChatAnthropic": "Anthropic API",
    "@n8n/n8n-nodes-langchain.vectorStorePinecone": "Pinecone API",
}
PREDEFINED_CREDENTIAL_LABELS = {"hubspotAppToken": "HubSpot App Token"}
AI_HOSTS = ("generativelanguage.googleapis.com", "api.openai.com", "api.anthropic.com")

DECISION_TYPES = {
    "n8n-nodes-base.if", "n8n-nodes-base.switch", "n8n-nodes-base.filter",
    "@n8n/n8n-nodes-langchain.textClassifier",
}

# Node references inside expressions and Code nodes: $('Name'), $("Name"), $node["Name"], $items("Name")
NODE_REF_RE = re.compile(
    r"""\$\(\s*(['"])(?P<a>.+?)\1\s*\)"""
    r"""|\$node\[\s*(['"])(?P<b>.+?)\3\s*\]"""
    r"""|\$items\(\s*(['"])(?P<c>.+?)\5"""
)

PLACEHOLDER_RES = (
    re.compile(r"\[(?:PLACEHOLDER[^\]\n]*|[A-Z][A-Z0-9 _.,'/&-]{2,}[A-Z0-9])\]"),
    re.compile(r"\b(?:YOUR|REPLACE_WITH)_[A-Z0-9_]+\b"),
    re.compile(r"\bN8N_BASE_URL\b"),
    re.compile(r"\bclient-slug\b"),
)


def load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump_json(obj: Any) -> str:
    """The one serialisation used for library files, so output is byte-stable."""
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def load_catalog(path: Path = CATALOG_PATH) -> list[dict]:
    import tomllib

    with open(path, "rb") as fh:
        return tomllib.load(fh)["workflow"]


def workflow_files(base: Path = WORKFLOWS_DIR) -> list[Path]:
    return sorted(Path(base).rglob("*.json"))


def iter_strings(obj: Any, path: tuple = (), include_keys: bool = True) -> Iterator[tuple[tuple, str]]:
    """Yield (path, text) for every string; dict keys are included unless include_keys is False."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if include_keys and isinstance(k, str):
                yield path + (k,), k
            yield from iter_strings(v, path + (k,), include_keys)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from iter_strings(v, path + (i,), include_keys)
    elif isinstance(obj, str):
        yield path, obj


def map_strings(obj: Any, fn) -> Any:
    """Return a copy of obj with fn applied to every string, dict keys included."""
    if isinstance(obj, dict):
        return {(fn(k) if isinstance(k, str) else k): map_strings(v, fn) for k, v in obj.items()}
    if isinstance(obj, list):
        return [map_strings(v, fn) for v in obj]
    if isinstance(obj, str):
        return fn(obj)
    return obj


def real_nodes(wf: dict) -> list[dict]:
    return [n for n in wf.get("nodes", []) if n.get("type") != STICKY]


def is_trigger(node: dict) -> bool:
    t = node.get("type", "")
    return t in TRIGGER_LABELS or t.lower().endswith("trigger")


def triggers(wf: dict) -> list[dict]:
    return [n for n in real_nodes(wf) if is_trigger(n)]


def trigger_label(node: dict) -> str:
    return TRIGGER_LABELS.get(node["type"], node["type"].split(".")[-1])


def trigger_detail(node: dict) -> str:
    """Human description of a trigger, e.g. 'Webhook, POST /webhook/lead-in'."""
    p = node.get("parameters", {})
    label = trigger_label(node)
    t = node["type"]
    if t == "n8n-nodes-base.webhook":
        return f"{label}, {p.get('httpMethod', 'GET')} /webhook/{p.get('path', '')}"
    if t == "n8n-nodes-base.formTrigger":
        title = p.get("formTitle")
        return f"{label} (n8n hosted form{', ' + repr(title) if title else ''})"
    if t == "n8n-nodes-base.scheduleTrigger":
        return f"{label} ({describe_schedule(p.get('rule', {}))})"
    if t == "n8n-nodes-base.gmailTrigger":
        q = (p.get("filters") or {}).get("q")
        return f"{label} (polls every minute{', query: ' + q if q else ''})"
    return label


def describe_schedule(rule: dict) -> str:
    parts = []
    for iv in rule.get("interval", []):
        field = iv.get("field", "days")
        if field == "hours":
            n = iv.get("hoursInterval", 1)
            parts.append("every hour" if n == 1 else f"every {n} hours")
        elif field == "days":
            n = iv.get("daysInterval", 1)
            at = iv.get("triggerAtHour")
            when = f" at {at:02d}:{iv.get('triggerAtMinute', 0):02d}" if at is not None else ""
            parts.append(("every day" if n == 1 else f"every {n} days") + when)
        elif field == "minutes":
            parts.append(f"every {iv.get('minutesInterval', 1)} minutes")
        else:
            parts.append(field)
    return ", ".join(parts) or "schedule"


def http_host(url: str) -> str:
    m = re.match(r"=?\s*https?://([^/:?#\s{}]+)", url or "")
    return m.group(1).lower() if m else ""


def credentials_needed(wf: dict) -> dict[str, list[str]]:
    """Credential label -> node names, derived from node types and auth settings."""
    out: dict[str, list[str]] = {}

    def add(label: str, node: dict) -> None:
        out.setdefault(label, []).append(node["name"])

    for n in real_nodes(wf):
        t, p = n["type"], n.get("parameters", {})
        if t == "n8n-nodes-base.httpRequest":
            auth = p.get("authentication")
            if auth == "genericCredentialType":
                kind = {"httpHeaderAuth": "Header Auth", "httpBasicAuth": "Basic Auth",
                        "httpQueryAuth": "Query Auth", "oAuth2Api": "OAuth2"}.get(p.get("genericAuthType"), "Generic auth")
                host = http_host(p.get("url", "")) or "the API"
                add(f"{kind} for {host}", n)
            elif auth == "predefinedCredentialType":
                ct = p.get("nodeCredentialType", "")
                add(PREDEFINED_CREDENTIAL_LABELS.get(ct, ct), n)
            continue
        if t == "@n8n/n8n-nodes-langchain.mcpClientTool":
            if p.get("authentication") in ("headerAuth", "bearerAuth"):
                add("Header Auth for your MCP server", n)
            continue
        if t in CREDENTIALS_BY_TYPE:
            add(CREDENTIALS_BY_TYPE[t], n)
    return dict(sorted(out.items()))


def uses_ai(wf: dict) -> bool:
    for n in real_nodes(wf):
        if n["type"].startswith("@n8n/n8n-nodes-langchain."):
            return True
        if n["type"] == "n8n-nodes-base.httpRequest" and http_host(n.get("parameters", {}).get("url", "")) in AI_HOSTS:
            return True
    return False


def placeholders(wf: dict, ignore: tuple[str, ...] = ()) -> dict[str, list[str]]:
    """Placeholder token -> node names it appears in (sticky notes excluded)."""
    found: dict[str, list[str]] = {}
    for n in real_nodes(wf):
        seen: set[str] = set()
        for _, text in iter_strings(n.get("parameters", {})):
            for rx in PLACEHOLDER_RES:
                for m in rx.finditer(text):
                    tok = m.group(0)
                    if tok not in ignore and tok not in seen:
                        seen.add(tok)
                        found.setdefault(tok, []).append(n["name"])
    return dict(sorted(found.items(), key=lambda kv: kv[0].lower()))


def empty_pickers(wf: dict) -> list[tuple[str, str, str]]:
    """Resource locators left empty on purpose: (node name, parameter, hint)."""
    out = []
    for n in real_nodes(wf):
        for key, val in (n.get("parameters") or {}).items():
            if isinstance(val, dict) and val.get("__rl") and val.get("value") in ("", None):
                out.append((n["name"], key, val.get("cachedResultName", "")))
    return out


def node_refs(text: str) -> set[str]:
    refs = set()
    for m in NODE_REF_RE.finditer(text):
        refs.add(m.group("a") or m.group("b") or m.group("c"))
    return refs


# ---------------------------------------------------------------- mermaid

def _label(text: str) -> str:
    return text.replace('"', "#quot;").replace("\n", " ")


def _shape(node: dict, idx: str) -> str:
    lab = _label(node["name"])
    t = node["type"]
    if is_trigger(node):
        return f'{idx}(["{lab}"])'
    if t in DECISION_TYPES:
        return f'{idx}{{"{lab}"}}'
    if t.startswith("@n8n/n8n-nodes-langchain."):
        return f'{idx}[["{lab}"]]'
    return f'{idx}["{lab}"]'


def output_labels(node: dict) -> list[str]:
    t, p = node["type"], node.get("parameters", {})
    labels: list[str] = []
    if t in ("n8n-nodes-base.if",):
        labels = ["true", "false"]
    elif t == "n8n-nodes-base.switch":
        for i, rule in enumerate((p.get("rules") or {}).get("values", [])):
            labels.append(rule.get("outputKey") if rule.get("renameOutput") else str(i))
    elif t == "@n8n/n8n-nodes-langchain.textClassifier":
        labels = [c.get("category", str(i)) for i, c in enumerate((p.get("categories") or {}).get("categories", []))]
    if node.get("onError") == "continueErrorOutput":
        base = len(labels) if labels else 1
        labels = labels or [""]
        labels += [""] * (base - len(labels)) + ["error"]
    return labels


AI_EDGE_LABELS = {
    "ai_languageModel": "model", "ai_tool": "tool", "ai_embedding": "embeddings",
    "ai_outputParser": "parser", "ai_memory": "memory", "ai_vectorStore": "vector store",
    "ai_document": "document", "ai_textSplitter": "splitter",
}


def mermaid(wf: dict) -> str:
    nodes = real_nodes(wf)
    ids = {n["name"]: f"n{i + 1}" for i, n in enumerate(nodes)}
    by_name = {n["name"]: n for n in nodes}
    lines = ["flowchart LR"]
    for n in nodes:
        lines.append("  " + _shape(n, ids[n["name"]]))
    for src, outs in wf.get("connections", {}).items():
        if src not in ids:
            continue
        for kind, lists in outs.items():
            labels = output_labels(by_name[src]) if kind == "main" else []
            for out_idx, targets in enumerate(lists or []):
                for tgt in targets or []:
                    if tgt.get("node") not in ids:
                        continue
                    a, b = ids[src], ids[tgt["node"]]
                    if kind == "main":
                        lab = labels[out_idx] if out_idx < len(labels) else ""
                        lab = re.sub(r"[^\w .-]", "", lab)
                        lines.append(f"  {a} -->|{lab}| {b}" if lab else f"  {a} --> {b}")
                    else:
                        lab = AI_EDGE_LABELS.get(kind, kind)
                        lines.append(f"  {a} -.->|{lab}| {b}")
    return "\n".join(lines)
