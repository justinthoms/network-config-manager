"""One interface for several AI providers.

Kinds:
  gemini      Google Gemini API (generateContent)
  anthropic   Anthropic Messages API
  openai, openrouter, groq, mistral, ollama, custom
              anything that speaks the OpenAI chat-completions format

Neutral message format used by callers:
  {"role": "user", "content": str}
  {"role": "assistant", "content": str | None, "tool_calls": [{"id", "name", "args"}], "raw": <provider data>}
  {"role": "tool", "tool_call_id": str, "name": str, "content": dict}
Tools are [{"name", "description", "parameters": <JSON schema, lowercase types>}].
"""
from __future__ import annotations

import json
import logging
import re
import time

import httpx

logger = logging.getLogger("ncm.llm")

PRESETS = {
    "gemini":     {"label": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta",
                   "needs_key": True, "key_url": "https://aistudio.google.com/apikey"},
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
                   "needs_key": True, "key_url": "https://openrouter.ai/keys"},
    "openai":     {"label": "OpenAI", "base_url": "https://api.openai.com/v1",
                   "needs_key": True, "key_url": "https://platform.openai.com/api-keys"},
    "groq":       {"label": "Groq", "base_url": "https://api.groq.com/openai/v1",
                   "needs_key": True, "key_url": "https://console.groq.com/keys"},
    "mistral":    {"label": "Mistral", "base_url": "https://api.mistral.ai/v1",
                   "needs_key": True, "key_url": "https://console.mistral.ai/api-keys"},
    "anthropic":  {"label": "Anthropic Claude", "base_url": "https://api.anthropic.com/v1",
                   "needs_key": True, "key_url": "https://console.anthropic.com/settings/keys"},
    "ollama":     {"label": "Ollama (local)", "base_url": "http://localhost:11434/v1",
                   "needs_key": False, "key_url": "https://ollama.com/download"},
    "custom":     {"label": "Custom (OpenAI compatible)", "base_url": "",
                   "needs_key": False, "key_url": ""},
}
OPENAI_LIKE = {"openai", "openrouter", "groq", "mistral", "ollama", "custom"}


class AIError(RuntimeError):
    """code: auth | model | rate | quota | network | other"""
    def __init__(self, message: str, code: str = "other"):
        super().__init__(message)
        self.code = code


class Conn:
    """What an adapter needs to talk to a provider."""
    def __init__(self, kind: str, base_url: str | None, api_key: str | None, name: str = "", provider_id=None):
        self.kind = kind
        self.base_url = (base_url or PRESETS.get(kind, {}).get("base_url") or "").rstrip("/")
        self.api_key = (api_key or "").strip()
        self.name = name or PRESETS.get(kind, {}).get("label", kind)
        self.provider_id = provider_id


# ───────────────────────── helpers ─────────────────────────

def _retry_after(resp) -> float | None:
    h = resp.headers.get("retry-after")
    if h:
        try:
            return float(h)
        except ValueError:
            pass
    try:
        for d in resp.json().get("error", {}).get("details", []):
            if str(d.get("@type", "")).endswith("RetryInfo") and d.get("retryDelay"):
                return float(str(d["retryDelay"]).rstrip("s"))
    except (ValueError, AttributeError, TypeError):
        pass
    m = re.search(r"retry in ([\d.]+)s", resp.text or "", re.IGNORECASE)
    return float(m.group(1)) if m else None


def _err_msg(resp) -> str:
    try:
        data = resp.json()
        err = data.get("error", data)
        if isinstance(err, dict):
            msg = err.get("message") or err.get("detail") or json.dumps(err)[:300]
            meta = err.get("metadata", {}) if isinstance(err.get("metadata"), dict) else {}
            if meta.get("raw"):
                msg += f" ({str(meta['raw'])[:200]})"
            return str(msg)
        return str(err)[:300]
    except ValueError:
        return (resp.text or "")[:300]


def _raise_for(conn: Conn, model: str, resp):
    msg = _err_msg(resp)
    st = resp.status_code
    if st in (401, 403):
        raise AIError(f"{conn.name} rejected the API key ({st}). Check the key. {msg[:160]}", "auth")
    if st == 404:
        raise AIError(f"{conn.name}: model '{model}' was not found. Pick another model. {msg[:160]}", "model")
    if st == 402:
        raise AIError(f"{conn.name}: no credits left for '{model}'. Choose a free model or add credits. {msg[:160]}", "quota")
    if st == 429:
        if "limit: 0" in msg or "limit:0" in msg:
            raise AIError(f"{conn.name}: model '{model}' has no free quota for this account. {msg[:200]}", "quota")
        daily = " Daily limit reached." if ("PerDay" in msg or "per day" in msg.lower() or "per-day" in msg.lower()) else ""
        raise AIError(f"{conn.name}: rate limit reached for '{model}'.{daily} {msg[:200]}", "rate")
    raise AIError(f"{conn.name} error {st}: {msg[:300]}", "other")


def _post(conn: Conn, model: str, url: str, body: dict, headers: dict, timeout: float):
    """POST with one polite retry on short rate limits."""
    waited = 0.0
    while True:
        try:
            resp = httpx.post(url, json=body, headers=headers, timeout=timeout)
        except httpx.HTTPError as exc:
            raise AIError(f"Cannot reach {conn.name} at {conn.base_url}: {type(exc).__name__} {exc}", "network") from exc
        if resp.status_code == 429:
            delay = _retry_after(resp)
            msg = _err_msg(resp)
            if delay is not None and delay <= 30 and waited < 45 and "limit: 0" not in msg:
                time.sleep(delay + 1)
                waited += delay + 1
                continue
        return resp


def parse_json(text: str) -> dict:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except ValueError:
        m = re.search(r"\{.*\}", t, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                pass
    raise AIError("The AI returned an answer NCM could not read. Try again or pick another model.", "other")


def to_gemini_schema(s):
    if isinstance(s, dict):
        out = {}
        for k, v in s.items():
            if k == "type" and isinstance(v, str):
                out[k] = v.upper()
            elif k in ("properties",):
                out[k] = {pk: to_gemini_schema(pv) for pk, pv in v.items()}
            else:
                out[k] = to_gemini_schema(v)
        return out
    if isinstance(s, list):
        return [to_gemini_schema(x) for x in s]
    return s


# ───────────────────────── Gemini ─────────────────────────

_gemini_auto: dict = {}        # provider_id -> substitute model when the chosen one is unavailable
_SKIP = re.compile(r"image|tts|audio|live|embedding|exp|computer|robotics|native|aqa|imagen|veo", re.IGNORECASE)


def _gemini_models(conn: Conn):
    resp = httpx.get(f"{conn.base_url}/models", params={"pageSize": 300},
                     headers={"x-goog-api-key": conn.api_key}, timeout=30)
    if resp.status_code != 200:
        _raise_for(conn, "-", resp)
    out = []
    for m in resp.json().get("models", []):
        if "generateContent" not in m.get("supportedGenerationMethods", []):
            continue
        mid = m.get("name", "").removeprefix("models/")
        if _SKIP.search(mid):
            continue
        out.append({"id": mid, "label": m.get("displayName") or mid, "tools": True,
                    "context": m.get("inputTokenLimit"), "free": None})
    return out


def _gemini_pick(conn: Conn, exclude: set):
    try:
        models = _gemini_models(conn)
    except AIError:
        return None
    best, score_best = None, None
    for m in models:
        mid = m["id"]
        if "flash" not in mid or mid in exclude:
            continue
        ver = re.search(r"gemini-(\d+(?:\.\d+)?)", mid)
        score = ("lite" not in mid, float(ver.group(1)) if ver else 0.0, "preview" not in mid, -len(mid))
        if score_best is None or score > score_best:
            best, score_best = mid, score
    return best


def _gemini_chat(conn: Conn, model, system, messages, tools, json_schema, max_tokens):
    contents, pending_tool = [], []
    def flush():
        if pending_tool:
            contents.append({"role": "user", "parts": list(pending_tool)})
            pending_tool.clear()
    for m in messages:
        if m["role"] == "tool":
            pending_tool.append({"functionResponse": {"name": m["name"], "response": {"result": m["content"]}}})
            continue
        flush()
        if m["role"] == "user":
            contents.append({"role": "user", "parts": [{"text": m["content"]}]})
        else:
            raw = m.get("raw") if m.get("raw_kind") == "gemini" else None
            if raw:
                contents.append({"role": "model", "parts": raw})
            elif m.get("tool_calls"):
                contents.append({"role": "model", "parts": [{"functionCall": {"name": c["name"], "args": c["args"]}}
                                                            for c in m["tool_calls"]]})
            else:
                contents.append({"role": "model", "parts": [{"text": m.get("content") or ""}]})
    flush()
    cfg = {"maxOutputTokens": max_tokens}
    if json_schema:
        cfg["responseMimeType"] = "application/json"
        cfg["responseSchema"] = to_gemini_schema(json_schema)
    body = {"system_instruction": {"parts": [{"text": system}]}, "contents": contents, "generationConfig": cfg}
    if tools:
        body["tools"] = [{"functionDeclarations": [
            {"name": t["name"], "description": t["description"], "parameters": to_gemini_schema(t["parameters"])}
            for t in tools]}]

    tried = {model}
    use = _gemini_auto.get(conn.provider_id) or model
    schema_dropped = False
    while True:
        if use.startswith("gemini-3"):
            cfg.pop("temperature", None)          # Gemini 3 is tuned for the default temperature
        else:
            cfg["temperature"] = 0.2
        resp = _post(conn, use, f"{conn.base_url}/models/{use}:generateContent", body,
                     {"x-goog-api-key": conn.api_key, "Content-Type": "application/json"}, 120)
        if resp.status_code == 200:
            break
        msg = _err_msg(resp)
        if resp.status_code == 400 and json_schema and not schema_dropped and "schema" in msg.lower():
            cfg.pop("responseSchema", None)
            schema_dropped = True
            continue
        if resp.status_code == 404 or (resp.status_code == 429 and "limit: 0" in msg):
            tried.add(use)
            alt = _gemini_pick(conn, tried) if len(tried) < 4 else None
            if alt:
                logger.warning("Gemini model %s unavailable, trying %s", use, alt)
                _gemini_auto[conn.provider_id] = alt
                use = alt
                continue
        _raise_for(conn, use, resp)

    data = resp.json()
    cand = (data.get("candidates") or [{}])[0]
    if cand.get("finishReason") in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST"):
        raise AIError(f"{conn.name} declined to answer ({cand.get('finishReason')}).", "other")
    parts = (cand.get("content") or {}).get("parts", [])
    calls = [{"id": f"{p['functionCall'].get('name')}-{i}", "name": p["functionCall"].get("name"),
              "args": p["functionCall"].get("args") or {}} for i, p in enumerate(parts) if "functionCall" in p]
    text = "".join(p.get("text", "") for p in parts if not p.get("thought") and "text" in p).strip()
    return {"text": text, "tool_calls": calls, "model": use, "truncated": cand.get("finishReason") == "MAX_TOKENS",
            "assistant": {"role": "assistant", "content": text, "tool_calls": calls, "raw": parts, "raw_kind": "gemini"}}


# ───────────────────────── OpenAI compatible ─────────────────────────

def _oa_headers(conn: Conn):
    h = {"Content-Type": "application/json"}
    if conn.api_key:
        h["Authorization"] = f"Bearer {conn.api_key}"
    if conn.kind == "openrouter":
        h["HTTP-Referer"] = "https://github.com/network-config-manager"
        h["X-Title"] = "NCM Network Config Manager"
    return h


def _oa_chat(conn: Conn, model, system, messages, tools, json_schema, max_tokens):
    sys_text = system
    if json_schema:
        sys_text += ("\n\nReturn ONLY one JSON object, no markdown, that follows this JSON schema:\n"
                     + json.dumps(json_schema))
    out = [{"role": "system", "content": sys_text}]
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": json.dumps(m["content"])[:60000]})
        else:
            if m.get("raw_kind") == "openai" and m.get("raw"):
                out.append(m["raw"])
            elif m.get("tool_calls"):
                out.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": [
                    {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                    for c in m["tool_calls"]]})
            else:
                out.append({"role": "assistant", "content": m.get("content") or ""})
    body = {"model": model, "messages": out, "max_tokens": max_tokens, "temperature": 0.2}
    if tools:
        body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                           "parameters": t["parameters"]}} for t in tools]
    if json_schema:
        body["response_format"] = {"type": "json_object"}
    timeout = 300 if conn.kind in ("ollama", "custom") else 120
    dropped = set()
    while True:
        resp = _post(conn, model, f"{conn.base_url}/chat/completions", body, _oa_headers(conn), timeout)
        if resp.status_code == 200:
            break
        msg = _err_msg(resp).lower()
        if resp.status_code in (400, 422):
            if "response_format" in body and "response_format" not in dropped and ("response_format" in msg or "json" in msg):
                body.pop("response_format"); dropped.add("response_format"); continue
            if "temperature" in body and "temperature" not in dropped and "temperature" in msg:
                body.pop("temperature"); dropped.add("temperature"); continue
            if "max_tokens" in body and "max_tokens" not in dropped and "max_tokens" in msg:
                body["max_completion_tokens"] = body.pop("max_tokens"); dropped.add("max_tokens"); continue
            if tools and ("tool" in msg or "function" in msg):
                raise AIError(f"{conn.name}: model '{model}' does not support tool calling, which the chat assistant "
                              "needs. Pick a model marked 'tools'.", "model")
        _raise_for(conn, model, resp)
    data = resp.json()
    if data.get("error"):
        raise AIError(f"{conn.name}: {str(data['error'])[:300]}", "other")
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    calls = []
    for c in msg.get("tool_calls") or []:
        fn = c.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {}
        calls.append({"id": c.get("id") or fn.get("name"), "name": fn.get("name"), "args": args if isinstance(args, dict) else {}})
    text = msg.get("content") or ""
    if isinstance(text, list):
        text = "".join(x.get("text", "") for x in text if isinstance(x, dict))
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    raw = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        raw["tool_calls"] = msg["tool_calls"]
    return {"text": text, "tool_calls": calls, "model": data.get("model") or model,
            "truncated": choice.get("finish_reason") == "length",
            "assistant": {"role": "assistant", "content": text, "tool_calls": calls, "raw": raw, "raw_kind": "openai"}}


def _oa_models(conn: Conn):
    url = f"{conn.base_url}/models"
    resp = httpx.get(url, headers=_oa_headers(conn), timeout=30)
    if resp.status_code != 200:
        _raise_for(conn, "-", resp)
    data = resp.json()
    items = data.get("data", data.get("models", [])) if isinstance(data, dict) else data
    out = []
    for m in items or []:
        mid = m.get("id") or m.get("name")
        if not mid:
            continue
        if conn.kind == "openai" and re.search(r"embedding|whisper|tts|dall-e|moderation|image|audio|realtime|transcribe|search|babbage|davinci", mid):
            continue
        if re.search(r"embed|whisper|tts|guard|moderation", mid, re.IGNORECASE) and conn.kind != "custom":
            continue
        free, tools = None, None
        pricing = m.get("pricing")
        if isinstance(pricing, dict):
            free = str(pricing.get("prompt", "1")) in ("0", "0.0") and str(pricing.get("completion", "1")) in ("0", "0.0")
        if mid.endswith(":free"):
            free = True
        if isinstance(m.get("supported_parameters"), list):
            tools = "tools" in m["supported_parameters"]
        out.append({"id": mid, "label": m.get("name") or mid, "free": free, "tools": tools,
                    "context": m.get("context_length") or m.get("context_window")})
    return out


# ───────────────────────── Anthropic ─────────────────────────

def _an_headers(conn: Conn):
    return {"x-api-key": conn.api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}


def _an_chat(conn: Conn, model, system, messages, tools, json_schema, max_tokens):
    sys_text = system
    if json_schema:
        sys_text += ("\n\nReturn ONLY one JSON object, no markdown, that follows this JSON schema:\n"
                     + json.dumps(json_schema))
    out, pending = [], []
    def flush():
        if pending:
            out.append({"role": "user", "content": list(pending)})
            pending.clear()
    for m in messages:
        if m["role"] == "tool":
            pending.append({"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": json.dumps(m["content"])[:60000]})
            continue
        flush()
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        elif m.get("raw_kind") == "anthropic" and m.get("raw"):
            out.append({"role": "assistant", "content": m["raw"]})
        elif m.get("tool_calls"):
            blocks = ([{"type": "text", "text": m["content"]}] if m.get("content") else []) + [
                {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]} for c in m["tool_calls"]]
            out.append({"role": "assistant", "content": blocks})
        else:
            out.append({"role": "assistant", "content": m.get("content") or "…"})
    flush()
    body = {"model": model, "system": sys_text, "messages": out, "max_tokens": max_tokens}
    if tools:
        body["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]
    resp = _post(conn, model, f"{conn.base_url}/messages", body, _an_headers(conn), 120)
    if resp.status_code != 200:
        _raise_for(conn, model, resp)
    data = resp.json()
    blocks = data.get("content", [])
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
    calls = [{"id": b["id"], "name": b["name"], "args": b.get("input") or {}} for b in blocks if b.get("type") == "tool_use"]
    return {"text": text, "tool_calls": calls, "model": data.get("model") or model,
            "truncated": data.get("stop_reason") == "max_tokens",
            "assistant": {"role": "assistant", "content": text, "tool_calls": calls, "raw": blocks, "raw_kind": "anthropic"}}


def _an_models(conn: Conn):
    resp = httpx.get(f"{conn.base_url}/models", params={"limit": 100}, headers=_an_headers(conn), timeout=30)
    if resp.status_code != 200:
        _raise_for(conn, "-", resp)
    return [{"id": m["id"], "label": m.get("display_name") or m["id"], "tools": True, "free": False, "context": None}
            for m in resp.json().get("data", [])]


# ───────────────────────── public API ─────────────────────────

def chat(conn: Conn, model: str, system: str, messages: list, tools=None, json_schema=None, max_tokens=8192) -> dict:
    if not model:
        raise AIError(f"No model selected for {conn.name}. Choose a model in the provider settings.", "model")
    if PRESETS.get(conn.kind, {}).get("needs_key") and not conn.api_key:
        raise AIError(f"{conn.name} has no API key.", "auth")
    if conn.kind == "gemini":
        return _gemini_chat(conn, model, system, messages, tools, json_schema, max_tokens)
    if conn.kind == "anthropic":
        return _an_chat(conn, model, system, messages, tools, json_schema, max_tokens)
    if conn.kind in OPENAI_LIKE:
        if not conn.base_url:
            raise AIError("Base URL is required for a custom provider.", "other")
        return _oa_chat(conn, model, system, messages, tools, json_schema, max_tokens)
    raise AIError(f"Unknown provider type {conn.kind}", "other")


def complete_json(conn: Conn, model: str, system: str, user_text: str, schema: dict, max_tokens=16384) -> tuple[dict, str]:
    res = chat(conn, model, system, [{"role": "user", "content": user_text}], json_schema=schema, max_tokens=max_tokens)
    try:
        return parse_json(res["text"]), res["model"]
    except AIError:
        if res.get("truncated"):
            raise AIError("The AI answer was cut off. Try again or pick a model with a larger output limit.", "other")
        raise


def list_models(conn: Conn) -> list[dict]:
    try:
        if conn.kind == "gemini":
            models = _gemini_models(conn)
        elif conn.kind == "anthropic":
            models = _an_models(conn)
        else:
            models = _oa_models(conn)
    except httpx.HTTPError as exc:
        raise AIError(f"Cannot reach {conn.name} at {conn.base_url}: {exc}", "network") from exc
    # free first (OpenRouter), then tool-capable, then name
    models.sort(key=lambda m: (m.get("free") is not True, m.get("tools") is False, m["id"]))
    return models


def test(conn: Conn, model: str) -> dict:
    t0 = time.time()
    res = chat(conn, model, "You are a connectivity test.", [{"role": "user", "content": "Reply with the single word OK."}],
               max_tokens=1024)
    return {"ok": True, "reply": res["text"][:80], "model": res["model"], "ms": int((time.time() - t0) * 1000)}
