# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""FreeLLMAPI is available as an OpenAI-compatible AI provider (URL + key only)."""
from app.services import llm


def test_freellmapi_preset_is_openai_compatible():
    assert "freellmapi" in llm.PRESETS
    p = llm.PRESETS["freellmapi"]
    assert p["needs_key"] is True and p["base_url"].endswith(":3001/v1")
    assert "freellmapi" in llm.OPENAI_LIKE                 # uses the OpenAI chat + model-list adapter


def test_conn_defaults_to_preset_url():
    c = llm.Conn("freellmapi", None, "freellmapi-key")
    assert c.base_url == "http://localhost:3001/v1"
    c2 = llm.Conn("freellmapi", "http://10.0.0.5:3001/v1/", "k")
    assert c2.base_url == "http://10.0.0.5:3001/v1"       # a custom host overrides, trailing slash trimmed


def test_presets_endpoint_lists_freellmapi(client, super_admin):
    presets = client.get("/api/ai/presets", headers=super_admin).json()
    assert "freellmapi" in presets and presets["freellmapi"]["label"] == "FreeLLMAPI"


def test_create_freellmapi_provider(client, super_admin):
    r = client.post("/api/ai/providers", headers=super_admin, json={
        "name": "Free LLM", "kind": "freellmapi", "base_url": "http://192.0.2.9:3001/v1",
        "api_key": "freellmapi-abc", "default_model": "auto", "enabled": True, "shared": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "freellmapi" and body["base_url"] == "http://192.0.2.9:3001/v1"
    # the key is required for this kind
    bad = client.post("/api/ai/providers", headers=super_admin, json={
        "name": "No key", "kind": "freellmapi", "base_url": "http://192.0.2.9:3001/v1", "shared": True})
    assert bad.status_code == 400
    client.delete(f"/api/ai/providers/{body['id']}", headers=super_admin)
