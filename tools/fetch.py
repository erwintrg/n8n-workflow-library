#!/usr/bin/env python3
"""Export workflows from your own n8n instance through the public REST API.

Read-only by design: the only HTTP method this script ever sends is GET.

    python tools/fetch.py --list
        Print id, name, tags and node count for every workflow (paginated).

    python tools/fetch.py --out RAW_DIR ID [ID ...]
        Save the full JSON of each workflow id to RAW_DIR/<id>.json.

    python tools/fetch.py --out RAW_DIR --manifest MANIFEST.json
        Same, for every "source" id listed in a sanitize config / manifest.

Configuration comes from the environment or a .env file:
    N8N_BASE_URL   e.g. https://n8n.example.com  (with or without /api/v1)
    N8N_API_KEY    an n8n API key (Settings > n8n API)

The raw export contains credential names, ids and everything else you put into
your workflows. Keep RAW_DIR outside this repository and run tools/sanitize.py
on it before anything is committed. The key and the base URL are never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path


def load_env(env_file: str | None) -> tuple[str, str]:
    """Return (api_root, api_key) from the environment or an .env file."""
    values: dict[str, str | None] = {}
    path = env_file or ".env"
    if Path(path).is_file():
        try:
            from dotenv import dotenv_values  # python-dotenv
        except ImportError:  # pragma: no cover - documented in requirements.txt
            sys.exit("python-dotenv is required to read an .env file (pip install python-dotenv)")
        values = dict(dotenv_values(path))
    base = values.get("N8N_BASE_URL") or os.environ.get("N8N_BASE_URL") or ""
    key = values.get("N8N_API_KEY") or os.environ.get("N8N_API_KEY") or ""
    if not base or not key:
        sys.exit("N8N_BASE_URL and N8N_API_KEY must be set (environment or .env)")
    base = base.rstrip("/")
    if not base.endswith("/api/v1"):
        base += "/api/v1"
    return base, key


def api_get(api_root: str, key: str, path: str, params: dict | None = None) -> dict:
    url = f"{api_root}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"X-N8N-API-KEY": key, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (own instance)
        return json.loads(resp.read().decode("utf-8"))


def list_workflows(api_root: str, key: str) -> list[dict]:
    items: list[dict] = []
    cursor = None
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        page = api_get(api_root, key, "/workflows", params)
        items.extend(page.get("data", []))
        cursor = page.get("nextCursor")
        if not cursor:
            return items


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="*", help="workflow ids to export")
    ap.add_argument("--env-file", help="path to an .env file (default: ./.env)")
    ap.add_argument("--list", action="store_true", help="list all workflows and exit")
    ap.add_argument("--json", action="store_true", help="with --list: print JSON instead of a table")
    ap.add_argument("--out", help="directory for raw exports (keep it outside the repo)")
    ap.add_argument("--manifest", help="sanitize config whose workflows[].source ids should be exported")
    args = ap.parse_args()

    api_root, key = load_env(args.env_file)

    if args.list:
        rows = []
        for wf in list_workflows(api_root, key):
            rows.append({
                "id": wf.get("id"),
                "name": wf.get("name"),
                "active": wf.get("active"),
                "archived": wf.get("isArchived", False),
                "tags": [t.get("name") for t in wf.get("tags") or []],
                "nodes": len(wf.get("nodes") or []),
                "updatedAt": wf.get("updatedAt"),
            })
        if args.json:
            print(json.dumps(rows, indent=2, ensure_ascii=False))
        else:
            for r in sorted(rows, key=lambda r: r["name"] or ""):
                tags = ",".join(r["tags"])
                flag = "A" if r["active"] else "-"
                arch = " [archived]" if r["archived"] else ""
                print(f"{r['id']}  {flag} {r['nodes']:>3}n  {r['name']}  [{tags}]{arch}")
        return 0

    ids = list(args.ids)
    if args.manifest:
        cfg = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        ids += [w["source"] for w in cfg.get("workflows", [])]
    if not ids or not args.out:
        ap.error("give --list, or --out plus workflow ids / --manifest")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for wf_id in ids:
        wf = api_get(api_root, key, f"/workflows/{urllib.parse.quote(wf_id)}")
        (out / f"{wf_id}.json").write_text(json.dumps(wf, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"saved {wf_id}: {wf.get('name')} ({len(wf.get('nodes') or [])} nodes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
