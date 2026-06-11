import httpx
import pytest

from backend.app.core.deepseek_provider import DeepSeekProvider
from backend.app.core.llm_provider import MockLLMProvider, SQLGenerationRequest


class FakeHTTPClient:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.requests = []

    def post(self, url, json, headers, timeout):
        self.requests.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return self.response


def test_deepseek_provider_requires_api_key():
    provider = DeepSeekProvider(api_key="", http_client=FakeHTTPClient(_response("SELECT 1")))

    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        provider.generate_sql(_request())


def test_deepseek_provider_posts_chat_completion_request():
    client = FakeHTTPClient(_response("SELECT order_id FROM fact_orders"))
    provider = DeepSeekProvider(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-pro",
        http_client=client,
        timeout=30,
    )

    result = provider.generate_sql(_request())

    assert result.provider == "deepseek"
    assert result.sql == "SELECT order_id FROM fact_orders"
    assert client.requests[0]["url"] == "https://api.deepseek.com/chat/completions"
    assert client.requests[0]["headers"]["Authorization"] == "Bearer test-key"
    assert client.requests[0]["json"]["model"] == "deepseek-v4-pro"
    assert client.requests[0]["json"]["stream"] is False
    assert client.requests[0]["json"]["messages"][0]["role"] == "system"
    assert client.requests[0]["json"]["messages"][1]["role"] == "user"
    assert "OUTPUT_FORMAT=sql" in client.requests[0]["json"]["messages"][1]["content"]
    timeout = client.requests[0]["timeout"]
    assert timeout.connect == 10.0
    assert timeout.read == 30.0
    assert timeout.write == 10.0
    assert timeout.pool == 5.0


def test_deepseek_provider_posts_olap_guidance_in_messages():
    client = FakeHTTPClient(_response("SELECT 1"))
    provider = DeepSeekProvider(api_key="test-key", http_client=client)

    provider.generate_sql(
        SQLGenerationRequest(
            question="查询销售额前10的商品同比增长",
            schema_context="# Schema Context",
            olap_intents=["topn", "yoy_mom"],
            olap_hint="TopN / YoY guidance",
        )
    )

    user_message = client.requests[0]["json"]["messages"][1]["content"]
    assert "Detected OLAP intents" not in user_message
    assert "OLAP SQL guidance:" in user_message
    assert "TopN / YoY guidance" in user_message


def test_deepseek_provider_strips_sql_markdown_fence():
    client = FakeHTTPClient(_response("```sql\nSELECT 1\n```"))
    provider = DeepSeekProvider(api_key="test-key", http_client=client)

    result = provider.generate_sql(_request())

    assert result.sql == "SELECT 1"


def test_deepseek_provider_parses_followup_json():
    client = FakeHTTPClient(
        _response(
            '{"sql": "SELECT COUNT(*) AS order_count FROM fact_orders", '
            '"is_follow_up": true, "change_kind": "metric"}'
        )
    )
    provider = DeepSeekProvider(api_key="test-key", http_client=client)

    result = provider.generate_sql(
        SQLGenerationRequest(
            question="换成订单数",
            schema_context="# Schema Context",
            prior_sql="SELECT SUM(payment_amount) AS sales_amount FROM fact_orders",
            prior_summary="Previous query",
        )
    )

    assert result.sql == "SELECT COUNT(*) AS order_count FROM fact_orders"
    assert result.is_follow_up is True
    assert result.change_kind == "metric"
    assert "OUTPUT_FORMAT=json" in client.requests[0]["json"]["messages"][1]["content"]


def test_deepseek_provider_rejects_bare_sql_when_structured_response_is_required():
    client = FakeHTTPClient(_response("SELECT order_id FROM fact_orders"))
    provider = DeepSeekProvider(api_key="test-key", http_client=client)

    with pytest.raises(ValueError, match="JSON object"):
        provider.generate_sql(
            SQLGenerationRequest(
                question="换成订单数",
                schema_context="# Schema Context",
                prior_sql="SELECT SUM(payment_amount) AS sales_amount FROM fact_orders",
                prior_summary="Previous query",
            )
        )


def test_deepseek_provider_rejects_bare_sql_for_standalone_turn_with_prior_context():
    client = FakeHTTPClient(_response("SELECT order_id FROM fact_orders"))
    provider = DeepSeekProvider(api_key="test-key", http_client=client)

    with pytest.raises(ValueError, match="JSON object"):
        provider.generate_sql(
            SQLGenerationRequest(
                question="列出订单",
                schema_context="# Schema Context",
                prior_sql="SELECT SUM(payment_amount) AS sales_amount FROM fact_orders",
                prior_summary="Previous query",
            )
        )


def test_deepseek_provider_rejects_missing_message_content():
    payload = {"choices": [{"message": {"content": None}}]}
    client = FakeHTTPClient(
        httpx.Response(
            200,
            request=httpx.Request("POST", "https://api.deepseek.com/chat/completions"),
            json=payload,
        )
    )
    provider = DeepSeekProvider(api_key="test-key", http_client=client)

    with pytest.raises(ValueError, match="message content"):
        provider.generate_sql(_request())


def test_mock_provider_is_not_affected_by_deepseek_provider():
    result = MockLLMProvider().generate_sql(
        SQLGenerationRequest(
            question="查询最近30天每日销售额和订单数",
            schema_context="# Schema Context",
        )
    )

    assert result.provider == "mock"
    assert result.matched_query_id == "recent_30d_daily_sales"


def _request() -> SQLGenerationRequest:
    return SQLGenerationRequest(
        question="查询订单",
        schema_context="# Schema Context",
    )


def _response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        request=httpx.Request("POST", "https://api.deepseek.com/chat/completions"),
        json={
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": content,
                    }
                }
            ]
        },
    )
