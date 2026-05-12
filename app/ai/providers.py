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


# ---------------------------------------------------------------------------
# Recommended generation settings per model
# ---------------------------------------------------------------------------
# Each entry returns:
#   {
#     "max_tokens":        int   — output / completion token ceiling
#     "reasoning_effort":  str|None — "minimal"|"low"|"medium"|"high" (OpenAI only)
#     "timeout_sec":       int   — HTTP read timeout
#     "is_reasoning":      bool  — model bills hidden reasoning tokens
#     "supports_temp":     bool  — accepts a custom temperature
#     "effort_levels":     list[str] — values the model accepts (UI hint)
#   }
#
# Numbers are tuned for the daily brief (~1.5k visible tokens) plus a generous
# safety margin so reasoning budget never starves the visible reply.

_EFFORT_GPT5  = ["minimal", "low", "medium", "high"]
_EFFORT_OSER  = ["low", "medium", "high"]


def recommended_settings(provider: str, model: str) -> dict:
    """Return recommended generation settings for the given provider/model."""
    p = (provider or "").lower()
    m = (model or "").lower()

    # ---- OpenAI -----------------------------------------------------------
    if p in ("openai", "gpt"):
        # Reasoning families
        if m.startswith("gpt-5"):
            # gpt-5 supports the "minimal" effort tier — fastest + most tokens
            # available for the visible reply.
            tier = "nano" if "nano" in m else ("mini" if "mini" in m else "full")
            return {
                "max_tokens":       {"full": 16000, "mini": 12000, "nano": 8000}[tier],
                "reasoning_effort": "minimal",
                "timeout_sec":      {"full": 900,   "mini": 600,   "nano": 300}[tier],
                "is_reasoning":     True,
                "supports_temp":    False,
                "effort_levels":    _EFFORT_GPT5,
            }
        if m.startswith(("o1", "o3", "o4")):
            # o-series "pro" variants think substantially longer.
            is_pro = "pro" in m
            is_mini = "mini" in m and not is_pro
            return {
                "max_tokens":       32000 if is_pro else (12000 if is_mini else 24000),
                "reasoning_effort": "medium" if is_pro else "low",
                "timeout_sec":      3600 if is_pro else (900 if is_mini else 1800),
                "is_reasoning":     True,
                "supports_temp":    False,
                "effort_levels":    _EFFORT_OSER,
            }
        # Standard chat models (gpt-4o, gpt-4.1, etc.)
        return {
            "max_tokens":       4000,
            "reasoning_effort": None,
            "timeout_sec":      180,
            "is_reasoning":     False,
            "supports_temp":    True,
            "effort_levels":    [],
        }

    # ---- Anthropic --------------------------------------------------------
    if p in ("anthropic", "claude"):
        if "opus" in m:
            return {"max_tokens": 4000, "reasoning_effort": None,
                    "timeout_sec": 600, "is_reasoning": False,
                    "supports_temp": True, "effort_levels": []}
        if "haiku" in m:
            return {"max_tokens": 2000, "reasoning_effort": None,
                    "timeout_sec": 180, "is_reasoning": False,
                    "supports_temp": True, "effort_levels": []}
        # sonnet + everything else
        return {"max_tokens": 4000, "reasoning_effort": None,
                "timeout_sec": 300, "is_reasoning": False,
                "supports_temp": True, "effort_levels": []}

    # ---- Google -----------------------------------------------------------
    if p in ("google", "gemini"):
        if "pro" in m:
            return {"max_tokens": 4000, "reasoning_effort": None,
                    "timeout_sec": 600, "is_reasoning": False,
                    "supports_temp": True, "effort_levels": []}
        return {"max_tokens": 2000, "reasoning_effort": None,
                "timeout_sec": 180, "is_reasoning": False,
                "supports_temp": True, "effort_levels": []}

    # ---- Fallback ---------------------------------------------------------
    return {"max_tokens": 2000, "reasoning_effort": None,
            "timeout_sec": 180, "is_reasoning": False,
            "supports_temp": True, "effort_levels": []}


def _merge_overrides(rec: dict, overrides: dict | None) -> dict:
    """Merge user overrides on top of recommended defaults (None = use rec)."""
    if not overrides:
        return rec
    out = dict(rec)
    for k in ("max_tokens", "reasoning_effort", "timeout_sec"):
        v = overrides.get(k)
        if v is not None and v != "":
            out[k] = v
    return out


def _post_json(url: str, headers: dict, body: dict, timeout: int = 300) -> dict:
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


def call_anthropic(api_key: str, model: str, system: str, messages,
                   options: dict | None = None) -> str:
    if not api_key:
        raise AIError("Anthropic API key not configured.")
    m = model or "claude-sonnet-4-5"
    cfg = _merge_overrides(recommended_settings("anthropic", m), options)
    body = {
        "model": m,
        "max_tokens": int(cfg["max_tokens"]),
        "system": system,
        "messages": _coerce_messages(messages),
    }
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    resp = _post_json("https://api.anthropic.com/v1/messages", headers, body,
                      timeout=int(cfg["timeout_sec"]))
    try:
        parts = [b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text"]
        text = "".join(parts).strip()
        if text:
            return text
        return resp["content"][0]["text"].strip()
    except (KeyError, IndexError, TypeError):
        raise AIError(f"Unexpected Anthropic response: {json.dumps(resp)[:300]}")


def call_openai(api_key: str, model: str, system: str, messages,
                options: dict | None = None) -> str:
    if not api_key:
        raise AIError("OpenAI API key not configured.")
    full = [{"role": "system", "content": system}] + _coerce_messages(messages)
    m = model or "gpt-4o-mini"
    cfg = _merge_overrides(recommended_settings("openai", m), options)
    is_reasoning = bool(cfg.get("is_reasoning"))
    body = {
        "model": m,
        "messages": full,
    }
    # gpt-5 / o-series reasoning models reject custom temperature — only the
    # default (1) is supported. Older chat models accept 0 for determinism.
    # Reasoning models also need a much larger token ceiling because their
    # reasoning tokens are billed against the same budget as the visible reply.
    if is_reasoning:
        body["max_completion_tokens"] = int(cfg["max_tokens"])
        eff = cfg.get("reasoning_effort")
        if eff:
            body["reasoning_effort"] = str(eff)
    else:
        if cfg.get("supports_temp", True):
            body["temperature"] = 0
        body["max_tokens"] = int(cfg["max_tokens"])
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    timeout = int(cfg["timeout_sec"])
    resp = _post_json("https://api.openai.com/v1/chat/completions", headers, body, timeout=timeout)
    try:
        choice = resp["choices"][0]
        text = (choice.get("message", {}).get("content") or "").strip()
        if not text:
            # Reasoning models can hit max_completion_tokens with nothing left
            # for the visible reply ("finish_reason":"length").  Give the user
            # an actionable error instead of an empty brief.
            reason = choice.get("finish_reason") or "empty"
            usage = resp.get("usage", {}) or {}
            details = usage.get("completion_tokens_details", {}) or {}
            reasoning_tok = details.get("reasoning_tokens")
            extra = f" (reasoning_tokens={reasoning_tok})" if reasoning_tok else ""
            raise AIError(
                f"{model} returned an empty reply (finish_reason={reason}){extra}. "
                "Open ⚙ Advanced (next to the model picker) and raise "
                "Max output tokens or lower Reasoning effort, or switch to a "
                "non-reasoning model (e.g. gpt-4o)."
            )
        return text
    except (KeyError, IndexError, TypeError):
        raise AIError(f"Unexpected OpenAI response: {json.dumps(resp)[:300]}")


def call_gemini(api_key: str, model: str, system: str, messages,
                options: dict | None = None) -> str:
    if not api_key:
        raise AIError("Gemini API key not configured.")
    m = model or "gemini-2.5-flash"
    cfg = _merge_overrides(recommended_settings("google", m), options)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key}"
    contents = []
    for msg in _coerce_messages(messages):
        role = "model" if msg["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": msg["content"]}]})
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {"temperature": 0, "maxOutputTokens": int(cfg["max_tokens"])},
    }
    resp = _post_json(url, {"Content-Type": "application/json"}, body,
                      timeout=int(cfg["timeout_sec"]))
    try:
        return resp["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError):
        raise AIError(f"Unexpected Gemini response: {json.dumps(resp)[:300]}")


def call_provider(provider: str, api_key: str, model: str, system: str, messages,
                  options: dict | None = None) -> str:
    p = (provider or "").lower()
    if p in ("anthropic", "claude"):
        return call_anthropic(api_key, model, system, messages, options)
    if p in ("openai", "gpt"):
        return call_openai(api_key, model, system, messages, options)
    if p in ("google", "gemini"):
        return call_gemini(api_key, model, system, messages, options)
    raise AIError(f"Unknown provider: {provider}")


DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-5",
    "openai":    "gpt-4o",
    "google":    "gemini-2.5-flash",
}
