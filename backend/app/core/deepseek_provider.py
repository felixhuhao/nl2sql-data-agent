from __future__ import annotations

from typing import Any

import httpx

from backend.app.agent.prompts.semantic_grounding import (
    build_concept_extraction_messages,
    build_grounding_check_messages,
)
from backend.app.agent.prompts.sql_generation import build_sql_generation_messages
from backend.app.config import get_settings
from backend.app.agent.semantic_grounding import (
    ConceptExtractionRequest,
    ConceptExtractionResult,
    GroundingCheckRequest,
    GroundingCheckResult,
    SemanticGroundingVerifier,
    parse_concept_extraction_content,
    parse_grounding_check_content,
)
from backend.app.core.llm_provider import (
    SQLGenerationRequest,
    SQLGenerationResult,
    parse_sql_generation_content,
)


class DeepSeekProvider(SemanticGroundingVerifier):
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

        response = self._post_chat_messages(build_sql_generation_messages(request), timeout=self._timeout)
        response.raise_for_status()
        content = _extract_message_content(response.json())
        sql, is_follow_up, change_kind = parse_sql_generation_content(
            content,
            expect_structured=request.prior_sql is not None,
        )
        return SQLGenerationResult(
            sql=sql,
            provider=self.name,
            is_follow_up=is_follow_up,
            change_kind=change_kind,
        )

    def extract_required_concepts(self, request: ConceptExtractionRequest) -> ConceptExtractionResult:
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY is required for DeepSeekProvider.")

        response = self._post_chat_messages(
            build_concept_extraction_messages(request),
            timeout=self._timeout,
        )
        response.raise_for_status()
        return parse_concept_extraction_content(_extract_message_content(response.json()))

    def check_grounding(self, request: GroundingCheckRequest) -> GroundingCheckResult:
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY is required for DeepSeekProvider.")

        response = self._post_chat_messages(
            build_grounding_check_messages(request),
            timeout=self._timeout,
        )
        response.raise_for_status()
        return parse_grounding_check_content(_extract_message_content(response.json()))

    def _post_chat_messages(self, messages: list[dict[str, str]], *, timeout: float) -> httpx.Response:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"

        timeout_config = _timeout_config(timeout)
        if self._http_client is not None:
            return self._http_client.post(url, json=payload, headers=headers, timeout=timeout_config)
        with httpx.Client(timeout=timeout_config) as client:
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
