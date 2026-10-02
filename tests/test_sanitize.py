"""The sanitizer on a fake raw export (tests/fixtures/raw), with the example config."""
import n8nlib as lib
import sanitize as san
import validate as val

FIXTURE = lib.ROOT / "tests" / "fixtures" / "raw" / "demo-lead-router.json"
CONFIG = lib.load_json(lib.ROOT / "sanitize.config.example.json")


def run() -> dict:
    return san.Sanitizer(CONFIG).workflow(lib.load_json(FIXTURE), slug="demo-lead-router", name="Demo lead router")


def test_only_allowlisted_structure_survives():
    wf = run()
    assert list(wf) == ["name", "nodes", "connections", "settings"]
    assert wf["settings"] == {"executionOrder": "v1"}
    for node in wf["nodes"]:
        assert not {"id", "webhookId", "credentials"} & set(node)


def test_private_values_are_replaced():
    text = lib.dump_json(run())
    for leaked in ("acme-example.com", "Jane Example", "Acme Example Co", "CLIENTSLUG", "—",
                   "XXXXXXXXXXXXXXXX", "1FakeSheetIdForTestsOnly", "555 010", "FakeCredential"):
        assert leaked not in text, leaked
    assert "N8N_BASE_URL/webhook/client-slug-intake?id=" in text
    assert '"value": "YOUR_SHEET_ID"' in text
    assert "Bearer YOUR_API_KEY" in text
    assert "client@example.com" in text and "someone@example.com" in text


def test_renamed_nodes_stay_wired():
    wf = run()
    names = {n["name"] for n in wf["nodes"]}
    assert {"Notify the client", "Webhook: client-slug-intake"} <= names
    assert wf["connections"]["Upsert contact"]["main"][0][0]["node"] == "Notify the client"
    assert "Webhook: client-slug-intake" in wf["connections"]
    assert val.validate_workflow(wf, "sanitized") == []


def test_output_is_deterministic():
    assert lib.dump_json(run()) == lib.dump_json(run())


def test_raw_export_fails_validation_for_the_right_reasons():
    problems = "\n".join(val.validate_workflow(lib.load_json(FIXTURE), "raw"))
    for expected in ("top-level key not allowed: id", "key not allowed: credentials", "key not allowed: webhookId",
                     "real-looking email", "CLIENTSLUG", "em or en dash", "URL host not on the allowlist",
                     "documentId holds a literal id", "holds a literal secret", "phone number"):
        assert expected in problems, expected


def test_uuid_webhook_paths_become_slugs():
    raw = {"name": "x", "nodes": [{"name": "Hook", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
                                   "position": [0, 0], "parameters": {"path": "0f0e0d0c-0b0a-4000-8000-000000000000"}}],
           "connections": {}}
    wf = san.Sanitizer({}).workflow(raw, slug="lead-in")
    assert wf["nodes"][0]["parameters"]["path"] == "lead-in"


def test_secret_patterns_in_text_are_replaced():
    # built at runtime so the repository holds no secret-shaped literal
    key = "sk-" + "a" * 32
    raw = {"name": "x", "nodes": [{"name": "Code", "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [0, 0],
                                   "parameters": {"jsCode": f"const k = '{key}';"}}], "connections": {}}
    wf = san.Sanitizer({}).workflow(raw, slug="x")
    assert key not in wf["nodes"][0]["parameters"]["jsCode"]
    assert "YOUR_API_KEY" in wf["nodes"][0]["parameters"]["jsCode"]


def test_colliding_renames_are_refused():
    raw = {"name": "x", "nodes": [
        {"name": "Email Ann", "type": "n8n-nodes-base.noOp", "typeVersion": 1, "position": [0, 0], "parameters": {}},
        {"name": "Email Bob", "type": "n8n-nodes-base.noOp", "typeVersion": 1, "position": [0, 0], "parameters": {}},
    ], "connections": {}}
    s = san.Sanitizer({"replace": [["Ann", "the client"], ["Bob", "the client"]]})
    try:
        s.workflow(raw, slug="x")
    except ValueError as e:
        assert "collide" in str(e)
    else:
        raise AssertionError("expected a collision error")
