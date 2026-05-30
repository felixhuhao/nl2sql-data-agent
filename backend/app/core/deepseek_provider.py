from __future__ import annotations

import re
from typing import Any

import httpx

from backend.app.config import get_settings
from backend.app.core.llm_provider import SQLGenerationRequest, SQLGenerationResult


class DeepSeekProvider:
    name = "deepseek"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        http_client: httpx.Client | None = None,
        timeout: float = 60.0,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key if api_key is not None else settings.deepseek_api_key
        self.base_url = (base_url or settings.deepseek_base_url).rstrip("/")
        self.model = model or settings.deepseek_model
        self._http_client = http_client
        self._timeout = timeout

    def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResult:
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY is required for DeepSeekProvider.")

        response = self._post_chat_completion(request)
        response.raise_for_status()
        content = _extract_message_content(response.json())
        return SQLGenerationResult(
            sql=_strip_sql_fence(content),
            provider=self.name,
        )

    def _post_chat_completion(self, request: SQLGenerationRequest) -> httpx.Response:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You generate DuckDB SQL for a governed NL2SQL system. "
                        "Return SQL only. Do not include markdown or explanation."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Schema context:\n{request.schema_context}\n\n"
                        f"Question:\n{request.question}"
                    ),
                },
            ],
            "temperature": 0,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"

        if self._http_client is not None:
            return self._http_client.post(url, json=payload, headers=headers, timeout=self._timeout)
        with httpx.Client(timeout=self._timeout) as client:
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


def _strip_sql_fence(content: str) -> str:
    match = re.fullmatch(r"```(?:sql)?\s*(.*?)\s*```", content.strip(), flags=re.IGNORECASE | re.DOTALL)
    if match is not None:
        return match.group(1).strip()
    return content.strip()
