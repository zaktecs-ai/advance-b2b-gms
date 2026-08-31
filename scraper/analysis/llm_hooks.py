"""Personalized AI pitch-hook generation — optional, backward-compatible.

Two modes, selected automatically at runtime:

  1. **AI mode** (API key present AND ``ai_hook.enabled: true``): the complete
     available business context (name, category, rating, review_count, review
     snippets, sentiment, keywords, city/state, top review, social presence,
     website, tech signals) is sent to the configured LLM provider
     (OpenAI / DeepSeek) to generate a **context-aware, personalized** hook.

  2. **Rule-based mode** (no key, or AI disabled, or the LLM call fails): the
     existing ``analysis.engine.pitch_hook()`` is used unchanged. Zero breakage.

The provider, model, and toggle live in ``config.yaml``; the API key lives in
``.env``. This is the single central control point — no other code changes are
needed to switch modes later.
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

# Supported providers -> (env var for API key, chat-completions endpoint).
_PROVIDERS: dict[str, dict] = {
    "openai": {
        "key_env": "OPENAI_API_KEY",
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "default_model": "gpt-4o-mini",
    },
    "deepseek": {
        "key_env": "DEEPSEEK_API_KEY",
        "endpoint": "https://api.deepseek.com/chat/completions",
        "default_model": "deepseek-chat",
    },
}


class LLMHookGenerator:
    """Generates personalized pitch hooks via an LLM when configured."""

    def __init__(self, enabled: bool = False, provider: str = "openai",
                 model: str = "", api_key_env: str = "", timeout: float = 30.0,
                 fallback_fn=None):
        self.enabled = enabled
        self.provider = (provider or "openai").lower()
        self.model = model
        self._api_key_env = api_key_env
        self._timeout = timeout
        self._fallback_fn = fallback_fn

        spec = _PROVIDERS.get(self.provider, _PROVIDERS["openai"])
        self._endpoint = spec["endpoint"]
        self._default_model = spec["default_model"]
        # Resolve the API key from the configured env var (default provider's).
        key_var = api_key_env or spec["key_env"]
        self._api_key = os.environ.get(key_var, "").strip()

    @property
    def is_active(self) -> bool:
        """True when AI mode is actually available (enabled + key present)."""
        return bool(self.enabled and self._api_key)

    def generate(self, record: dict) -> str | None:
        """Generate a personalized hook, or return None to fall back.

        Returns None when AI is unavailable or the call fails, so the caller
        can seamlessly use the rule-based hook.
        """
        if not self.is_active:
            return None
        prompt = self._build_prompt(record)
        try:
            payload = {
                "model": self.model or self._default_model,
                "messages": [
                    {"role": "system",
                     "content": (
                         "You write short, specific, personalized cold-outreach "
                         "opening lines for B2B lead generation. Use the facts "
                         "provided; never invent a detail. One or two sentences, "
                         "no emojis, no placeholders, no markdown.")},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.6,
                "max_tokens": 120,
            }
            response = self._post(payload)
            hook = self._parse_response(response)
            return hook or None
        except Exception as e:  # noqa: BLE001 — any failure falls back
            log.warning("LLM hook generation failed (%s); using rule-based hook",
                        type(e).__name__)
            return None

    def _build_prompt(self, record: dict) -> str:
        r = record
        lines = [
            "Write a personalized outreach opening line for this business:",
            f"Name: {r.get('business_name') or 'N/A'}",
            f"Category: {r.get('category') or 'N/A'}",
            f"Location: {r.get('city') or ''}, {r.get('state') or ''}",
            f"Rating: {r.get('rating') or 'N/A'}",
            f"Review count: {r.get('review_count') or 'N/A'}",
        ]
        if r.get("sentiment_score") not in (None, "N/A", "", "0"):
            lines.append(f"Sentiment score: {r.get('sentiment_score')}")
        if r.get("review_keywords"):
            lines.append(f"Common praise/topics in reviews: {r.get('review_keywords')}")
        if r.get("top_review"):
            lines.append(f"Sample review: {str(r.get('top_review'))[:300]}")
        socials = [k for k in ("facebook", "instagram", "linkedin") if r.get(k) not in (None, "N/A", "")]
        if socials:
            lines.append(f"Active social platforms: {', '.join(socials)}")
        if r.get("cms") and r.get("cms") != "N/A":
            lines.append(f"Website platform: {r.get('cms')}")
        lines.append(
            "The hook should acknowledge something specific about this business "
            "(its reputation, review themes, or niche) and pivot to a value offer. "
            "Return ONLY the hook text."
        )
        return "\n".join(lines)

    def _post(self, payload: dict) -> Any:
        import json
        import urllib.request
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._endpoint, data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _parse_response(self, response: Any) -> str | None:
        try:
            content = response["choices"][0]["message"]["content"]
            hook = (content or "").strip().strip('"').strip()
            hook = hook.replace("\n", " ").strip()
            return hook or None
        except (KeyError, IndexError, TypeError):
            return None
