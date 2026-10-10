"""LLM provider abstraction and the OpenAI-compatible implementation (OpenRouter by default).

The rest of the system only ever sees :class:`LLMProvider` and :class:`~core.models.LLMResponse`.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from typing import Any, Protocol, TypeVar

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import BaseModel, ValidationError

from core.config import Settings
from core.models import LLMResponse, LLMToolCall, Usage

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class LLMError(RuntimeError):
    """Raised when the provider fails after retries or returns an unusable response."""


class QuotaExhaustedError(LLMError):
    """A rate limit that will not clear by retrying now (daily quota, insufficient credits)."""


def _is_quota_error(exc: APIStatusError) -> bool:
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    meta = error.get("metadata") if isinstance(error.get("metadata"), dict) else {}
    message = str(error.get("message") or exc.message or "").lower()
    source = str(meta.get("limit_source") or "").lower()
    if exc.status_code == 402:
        return True
    return exc.status_code == 429 and (
        "per-day" in message or "daily" in source or "credits" in message
    )


class LLMProvider(Protocol):
    """Chat-completion provider with optional tools and structured output."""

    default_model: str

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.0,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Run one chat completion."""
        ...


def strip_code_fences(text: str) -> str:
    """Remove ``` fences a model may wrap JSON in."""
    return _FENCE.sub("", text.strip()).strip()


def repair_json(text: str) -> str:
    """Fix the most common model JSON slips outside of string literals.

    * thousands separators in bare numbers (``"value": 96,773`` → ``96773``)
    * trailing commas before ``}`` / ``]``
    String contents are never touched.
    """
    out: list[str] = []
    in_string = False
    escape = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "," and out and out[-1].isdigit():
            j = i + 1
            digits = 0
            while j < n and text[j].isdigit():
                digits += 1
                j += 1
            if digits == 3 and (j >= n or not text[j].isdigit()):
                i += 1  # drop the separator
                continue
        if ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1  # trailing comma
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from model text, tolerating fences, leading prose and number slips."""
    cleaned = strip_code_fences(text)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    candidates = [cleaned]
    if start >= 0 and end > start:
        candidates.append(cleaned[start : end + 1])
    candidates += [repair_json(c) for c in list(candidates)]
    last: Exception | None = None
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            break
        except json.JSONDecodeError as exc:
            last = exc
    else:
        raise last  # type: ignore[misc]
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def structured(
    provider: LLMProvider,
    messages: list[dict[str, Any]],
    model_cls: type[T],
    *,
    schema_name: str,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float = 0.0,
) -> tuple[T, Usage, str]:
    """Ask for JSON matching ``model_cls`` and validate it, retrying once with the error.

    Returns the parsed model, accumulated usage and the model id that answered.
    """
    schema = model_cls.model_json_schema()
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": schema_name, "strict": False, "schema": schema},
    }
    usage = Usage()
    attempt_messages = list(messages)
    last_error: Exception | None = None
    answered_by = model or provider.default_model
    for attempt in range(2):
        response = provider.chat(
            attempt_messages,
            response_format=response_format,
            temperature=temperature,
            model=model,
            max_tokens=max_tokens,
        )
        usage = usage + response.usage
        answered_by = response.model
        try:
            data = parse_json_object(response.content or "")
            return model_cls.model_validate(data), usage, answered_by
        except (ValueError, ValidationError) as exc:
            last_error = exc
            log.warning(
                "structured output invalid (attempt %d): %s | content: %r",
                attempt + 1,
                str(exc)[:200],
                (response.content or "")[:300],
            )
            attempt_messages = [
                *messages,
                {"role": "assistant", "content": response.content or ""},
                {
                    "role": "user",
                    "content": (
                        "Your previous reply was not valid for the required JSON schema: "
                        f"{str(exc)[:800]}\nReply again with ONLY a JSON object that satisfies "
                        "the schema. No prose, no code fences."
                    ),
                },
            ]
    raise LLMError(f"structured output failed after retry: {last_error}")


class OpenAICompatibleProvider:
    """OpenRouter / OpenAI / Ollama via the ``openai`` SDK with retries and usage accounting."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.default_model = settings.llm_model
        api_key = settings.openrouter_api_key.get_secret_value()
        if not api_key:
            log.warning("OPENROUTER_API_KEY is empty — LLM calls will fail")
        self._client = OpenAI(
            api_key=api_key or "missing",
            base_url=settings.llm_base_url,
            timeout=settings.llm.timeout_s,
            max_retries=0,  # we retry ourselves to control backoff and logging
            default_headers={
                "HTTP-Referer": settings.llm_http_referer,
                "X-Title": settings.llm_app_title,
            },
        )

    # ---------------------------------------------------------------- public

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.0,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """One chat completion with exponential-backoff retries on transient failures."""
        model_id = model or self.default_model
        kwargs: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "temperature": temperature,
            "extra_body": self._extra_body(),
        }
        if tools:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if response_format is not None:
            kwargs["response_format"] = response_format
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        cfg = self._settings.llm
        last: Exception | None = None
        for attempt in range(cfg.max_retries + 1):
            try:
                raw = self._client.chat.completions.create(**kwargs)
                return self._normalise(raw, model_id)
            except (APITimeoutError, APIConnectionError) as exc:
                last = exc
            except APIStatusError as exc:
                last = exc
                if _is_quota_error(exc):
                    raise QuotaExhaustedError(self._describe(exc)) from exc
                if exc.status_code not in _RETRYABLE_STATUS:
                    raise LLMError(self._describe(exc)) from exc
            except LLMError as exc:
                last = exc
            if attempt < cfg.max_retries:
                delay = cfg.backoff_base_s * (2**attempt) + random.uniform(0, 0.5)
                log.warning(
                    "LLM call failed (%s); retry %d/%d in %.1fs",
                    type(last).__name__,
                    attempt + 1,
                    cfg.max_retries,
                    delay,
                )
                time.sleep(delay)
        raise LLMError(self._describe(last)) from last

    # ---------------------------------------------------------------- internals

    def _extra_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if self._settings.llm.usage_accounting == "openrouter":
            body["usage"] = {"include": True}
        if self._settings.llm.exclude_reasoning:
            body["reasoning"] = {"exclude": True}
        return body

    def _normalise(self, raw: Any, requested_model: str) -> LLMResponse:
        payload = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
        if "choices" not in payload or not payload["choices"]:
            # OpenRouter can return HTTP 200 with an error object
            err = payload.get("error") or payload
            raise LLMError(f"provider returned no choices: {json.dumps(err)[:400]}")
        choice = payload["choices"][0]
        message = choice.get("message") or {}
        tool_calls: list[LLMToolCall] = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            arguments = fn.get("arguments") or "{}"
            try:
                parsed = json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
            except json.JSONDecodeError:
                parsed = {"_raw": arguments}
            tool_calls.append(
                LLMToolCall(
                    id=call.get("id") or f"call_{len(tool_calls)}",
                    name=fn.get("name", ""),
                    arguments=parsed,
                )
            )
        usage = self._usage(payload.get("usage") or {}, payload.get("model") or requested_model)
        content = message.get("content")
        if isinstance(content, list):  # some providers return content parts
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            model=payload.get("model") or requested_model,
            finish_reason=choice.get("finish_reason"),
            raw=payload,
        )

    def _usage(self, usage: dict[str, Any], model_id: str) -> Usage:
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        total = int(usage.get("total_tokens") or prompt + completion)
        cost: float | None = None
        if "cost" in usage and usage["cost"] is not None:
            cost = float(usage["cost"])
        else:
            price = self._settings.llm.price_table.get(model_id)
            if price is not None:
                cost = (prompt * price.input + completion * price.output) / 1_000_000
        return Usage(
            prompt_tokens=prompt, completion_tokens=completion, total_tokens=total, cost_usd=cost
        )

    @staticmethod
    def _describe(exc: Exception | None) -> str:
        if isinstance(exc, APIStatusError):
            body = exc.body if isinstance(exc.body, dict) else {}
            msg = (
                (body.get("error") or {}).get("message")
                if isinstance(body.get("error"), dict)
                else None
            )
            return f"HTTP {exc.status_code}: {msg or exc.message}"[:500]
        return f"{type(exc).__name__}: {exc}"[:500]


def build_provider(settings: Settings) -> LLMProvider:
    """Factory used by the service layer."""
    return OpenAICompatibleProvider(settings)
