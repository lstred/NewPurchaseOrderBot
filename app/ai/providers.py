"""
Minimal HTTP clients for Anthropic / OpenAI / Google Gemini.
Uses stdlib urllib only — no extra dependencies (PyInstaller-friendly).

Each provider call accepts a `messages` list:
    [{"role": "user"|"assistant", "content": "..."}, ...]
plus a separate `system` prompt string.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error


class AIError(RuntimeError):
    pass


def _post_json(url: str, headers: dict, body: dict, timeout: int = 90) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="ignore")
        except Exception:
            err_body = ""
        raise AIError(f"HTTP {e.code}: {err_body[:600]}") from e
    except urllib.error.URLError as e:
        raise AIError(f"Network error: {e.reason}") from e


def _coerce_messages(messages_or_str) -> list[dict]:
    """Accept either a string (treated as a single user turn) or a list of {role,content} dicts."""
    if isinstance(messages_or_str, str):
        return [{"role": "user", "content": messages_or_str}]
    return [
        {"role": str(m.get("role", "user")), "content": str(m.get("content", ""))}
        for m in messages_or_str if m
    ]


def call_anthropic(api_key: str, model: str, system: str, messages) -> str:
    if not api_key:
        raise AIError("Anthropic API key not configured.")
    body = {
        "model": model or "claude-sonnet-4-5",
        "max_tokens": 1500,
        "system": system,
        "messages": _coerce_messages(messages),
    }
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    resp = _post_json("https://api.anthropic.com/v1/messages", headers, body)
    try:
        parts = [b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text"]
        text = "".join(parts).strip()
        if text:
            return text
        return resp["content"][0]["text"].strip()
    except (KeyError, IndexError, TypeError):
        raise AIError(f"Unexpected Anthropic response: {json.dumps(resp)[:300]}")


def call_openai(api_key: str, model: str, system: str, messages) -> str:
    if not api_key:
        raise AIError("OpenAI API key not configured.")
    full = [{"role": "system", "content": system}] + _coerce_messages(messages)
    body = {
        "model": model or "gpt-4o-mini",
        "messages": full,
        "temperature": 0,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    resp = _post_json("https://api.openai.com/v1/chat/completions", headers, body)
    try:
        return resp["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        raise AIError(f"Unexpected OpenAI response: {json.dumps(resp)[:300]}")


def call_gemini(api_key: str, model: str, system: str, messages) -> str:
    if not api_key:
        raise AIError("Gemini API key not configured.")
    m = model or "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key}"
    contents = []
    for msg in _coerce_messages(messages):
        role = "model" if msg["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": msg["content"]}]})
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {"temperature": 0, "maxOutputTokens": 1500},
    }
    resp = _post_json(url, {"Content-Type": "application/json"}, body)
    try:
        return resp["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError):
        raise AIError(f"Unexpected Gemini response: {json.dumps(resp)[:300]}")


def call_provider(provider: str, api_key: str, model: str, system: str, messages) -> str:
    p = (provider or "").lower()
    if p in ("anthropic", "claude"):
        return call_anthropic(api_key, model, system, messages)
    if p in ("openai", "gpt"):
        return call_openai(api_key, model, system, messages)
    if p in ("google", "gemini"):
        return call_gemini(api_key, model, system, messages)
    raise AIError(f"Unknown provider: {provider}")


DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-5",
    "openai":    "gpt-4o-mini",
    "google":    "gemini-2.5-flash",
}
