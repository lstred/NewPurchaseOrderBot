"""
Minimal HTTP clients for Anthropic / OpenAI / Google Gemini.
Uses stdlib urllib only — no extra dependencies (PyInstaller-friendly).
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error


class AIError(RuntimeError):
    pass


def _post_json(url: str, headers: dict, body: dict, timeout: int = 60) -> dict:
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
        raise AIError(f"HTTP {e.code}: {err_body[:500]}") from e
    except urllib.error.URLError as e:
        raise AIError(f"Network error: {e.reason}") from e


def call_anthropic(api_key: str, model: str, system: str, user: str) -> str:
    """Call Claude Messages API and return text content."""
    if not api_key:
        raise AIError("Anthropic API key not configured.")
    body = {
        "model": model or "claude-sonnet-4-5",
        "max_tokens": 1024,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    resp = _post_json("https://api.anthropic.com/v1/messages", headers, body)
    try:
        return resp["content"][0]["text"].strip()
    except (KeyError, IndexError, TypeError):
        raise AIError(f"Unexpected Anthropic response: {json.dumps(resp)[:300]}")


def call_openai(api_key: str, model: str, system: str, user: str) -> str:
    """Call OpenAI Chat Completions API."""
    if not api_key:
        raise AIError("OpenAI API key not configured.")
    body = {
        "model": model or "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
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


def call_gemini(api_key: str, model: str, system: str, user: str) -> str:
    """Call Google Gemini generateContent API."""
    if not api_key:
        raise AIError("Gemini API key not configured.")
    m = model or "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key}"
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 1024},
    }
    headers = {"Content-Type": "application/json"}
    resp = _post_json(url, headers, body)
    try:
        return resp["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError):
        raise AIError(f"Unexpected Gemini response: {json.dumps(resp)[:300]}")


def call_provider(provider: str, api_key: str, model: str, system: str, user: str) -> str:
    p = (provider or "").lower()
    if p in ("anthropic", "claude"):
        return call_anthropic(api_key, model, system, user)
    if p in ("openai", "gpt"):
        return call_openai(api_key, model, system, user)
    if p in ("google", "gemini"):
        return call_gemini(api_key, model, system, user)
    raise AIError(f"Unknown provider: {provider}")


# Suggested defaults
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-5",
    "openai": "gpt-4o-mini",
    "google": "gemini-2.5-flash",
}
