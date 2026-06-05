from __future__ import annotations

from typing import Any

import httpx

from backend.app.agent.prompts.sql_generation import build_sql_generation_messages
from backend.app.config import get_settings
from backend.app.core.llm_provider import (
    SQLGenerationRequest,
    SQLGenerationResult,
    infer_followup_change_kind,
    parse_sql_generation_content,
)


class DeepSeekProvider:
    name = "deepseek"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        http_client: httpx.Client | None = None,
        timeout: float | None = None,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key if api_key is not None else settings.deepseek_api_key
        self.base_url = (base_url or settings.deepseek_base_url).rstrip("/")
        self.model = model or settings.deepseek_model
        self._http_client = http_client
        self._timeout = timeout if timeout is not None else settings.deepseek_timeout

    def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResult:
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY is required for DeepSeekProvider.")

        response = self._post_chat_completion(request)
        response.raise_for_status()
        content = _extract_message_content(response.json())
        fallback_is_follow_up, fallback_change_kind = infer_followup_change_kind(request.question)
        sql, is_follow_up, change_kind = parse_sql_generation_content(
            content,
            expect_structured=request.prior_sql is not None,
            fallback_is_follow_up=request.prior_sql is not None and fallback_is_follow_up,
            fallback_change_kind=fallback_change_kind,
        )
        return SQLGenerationResult(
            sql=sql,
            provider=self.name,
            is_follow_up=is_follow_up,
            change_kind=change_kind,
        )

    def _post_chat_completion(self, request: SQLGenerationRequest) -> httpx.Response:
        payload = {
            "model": self.model,
            "messages": build_sql_generation_messages(request),
            "temperature": 0,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"

        timeout = _timeout_config(self._timeout)
        if self._http_client is not None:
            return self._http_client.post(url, json=payload, headers=headers, timeout=timeout)
        with httpx.Client(timeout=timeout) as client:
            return client.post(url, json=payload, headers=headers)


def _extract_message_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not choices:
        raise ValueError("DeepSeek response does not include choices.")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not content or not isinstance(content, str):
        raise ValueError("DeepSeek response does not include message content.")
    return content.strip()


def _timeout_config(timeout: float) -> httpx.Timeout:
    return httpx.Timeout(
        timeout=timeout,
        connect=min(10.0, timeout),
        read=timeout,
        write=min(10.0, timeout),
        pool=min(5.0, timeout),
    )
