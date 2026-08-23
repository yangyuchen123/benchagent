"""LLM client wrapper backed by litellm (unified multi-provider API).

Reads configuration from config/models.yaml (or env vars) and provides:
  - chat()        : plain chat completion
  - chat_json()   : completion with guaranteed JSON object output
  - chat_enum()   : completion constrained to one of several choices

Provider-specific quirks — max_tokens handling, json-mode prompt requirements,
parameter support differences, retries — are handled by litellm instead of
hand-rolled glue. Any litellm-supported provider can be used by pointing
LLM_API_BASE_URL / LLM_API_KEY at it (OpenAI, DeepSeek, local vLLM/Ollama, ...).
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

import litellm
from litellm import APIConnectionError, APIError, BadRequestError, RateLimitError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

# Reduce litellm's own chatter and be tolerant of per-provider parameter quirks.
litellm.suppress_debug_info = True
litellm.drop_params = True  # silently drop params a provider doesn't support


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> Any:
    """Best-effort JSON extraction: direct parse, then fenced/delimited blocks."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # ```json ... ```
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # First {...} or [...] block (balanced-brace scan)
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == open_ch:
                depth += 1
            elif text[i] == close_ch:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    raise LLMError(f"Could not extract JSON from LLM output:\n{text[:500]}")


class LLMClient:
    def __init__(self, config: dict[str, Any]):
        self.config = config or {}
        self.provider = config.get("provider") or "generic"
        self.api_key = (
            config.get("api_key")
            or os.environ.get("LLM_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
        self.base_url = config.get("base_url") or os.environ.get("LLM_API_BASE_URL")
        if not self.api_key:
            raise LLMError(
                "No API key configured. Set api_key in config/models.yaml or "
                "export LLM_API_KEY / OPENROUTER_API_KEY."
            )
        self.model = (
            config.get("model")
            or config.get("default")
            or config.get("default_model")
            or "gpt-4o"
        )

    def _model_arg(self, model: str) -> str:
        """Add the litellm provider prefix so the model routes to the right
        backend. Model ids that already carry a provider prefix pass through."""
        if self.provider == "openrouter":
            # OpenRouter model ids look like "stealth/ox-alpha" or "openai/gpt-4o";
            # litellm needs the "openrouter/" gateway prefix.
            if model.startswith("openrouter/"):
                return model
            return f"openrouter/{model}"
        # generic OpenAI-compatible endpoint (DeepSeek, vLLM, ...)
        if self.base_url and "/" not in model:
            return f"openai/{model}"
        return model

    def _complete(self, messages: list[dict], model: str, temperature: float,
                  max_tokens: Optional[int], json_mode: bool) -> str:
        kwargs: dict[str, Any] = dict(
            model=self._model_arg(model),
            messages=messages,
            temperature=temperature,
            api_key=self.api_key,
        )
        if self.base_url:
            kwargs["api_base"] = self.base_url
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if json_mode:
            # Some endpoints (e.g. DeepSeek) require the literal word "json" in the
            # prompt to enable response_format=json_object.
            full_prompt = "\n".join(m.get("content", "") for m in messages).lower()
            if "json" not in full_prompt:
                messages[-1]["content"] += "\n\nOutput must be a single valid JSON object."
            kwargs["response_format"] = {"type": "json_object"}
        resp = litellm.completion(**kwargs)
        content = resp.choices[0].message.content or ""
        if not content and max_tokens:
            # Belt-and-braces: some providers return empty content when max_tokens
            # is set; retry once without the limit.
            log.warning("LLM returned empty content with max_tokens=%s; retrying without it", max_tokens)
            kwargs.pop("max_tokens", None)
            resp = litellm.completion(**kwargs)
            content = resp.choices[0].message.content or ""
        return content

    @retry(
        retry=retry_if_exception_type((APIConnectionError, RateLimitError, APIError)),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def chat(self, system: str, user: str, *, model: Optional[str] = None,
             temperature: float = 0.2, max_tokens: Optional[int] = None) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return self._complete(messages, model or self.model, temperature, max_tokens, json_mode=False)

    @retry(
        retry=retry_if_exception_type((APIConnectionError, RateLimitError, APIError, LLMError)),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def chat_json(self, system: str, user: str, *, model: Optional[str] = None,
                  temperature: float = 0.2, max_tokens: Optional[int] = None) -> Any:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        text = self._complete(messages, model or self.model, temperature, max_tokens, json_mode=True)
        return _extract_json(text)

    @retry(
        retry=retry_if_exception_type((APIConnectionError, RateLimitError, APIError, LLMError)),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def chat_enum(self, system: str, user: str, choices: list[str], *,
                  model: Optional[str] = None, temperature: float = 0.0) -> str:
        choices_text = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(choices))
        text = self.chat(
            system=system,
            user=f"{user}\n\nReply with exactly one of the following choices (the number and text):\n{choices_text}",
            model=model,
            temperature=temperature,
            max_tokens=16,
        )
        for c in choices:
            if c.lower() in text.lower():
                return c
        # fall back to numeric index
        m = re.search(r"\b([1-9])\b", text)
        if m and int(m.group(1)) <= len(choices):
            return choices[int(m.group(1)) - 1]
        raise LLMError(f"Could not map LLM reply to a choice: {text!r}")
