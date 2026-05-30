from fastapi.testclient import TestClient

from backend.app import main


def test_retrieve_metadata_endpoint_returns_retrieval_result(monkeypatch):
    def fake_retrieve(question: str) -> dict:
        assert question == "按渠道统计销售额"
        return {
            "question": question,
            "normalized_question": "按渠道统计销售额",
            "fallback_used": False,
            "tables": [{"table_name": "fact_orders", "source": "metric_expansion"}],
            "columns": [{"table_name": "fact_orders", "column_name": "payment_amount"}],
            "metrics": [{"name": "sales_amount"}],
            "verified_queries": [],
        }

    monkeypatch.setattr("backend.app.api.metadata.retrieve_metadata_assets", fake_retrieve)
    client = TestClient(main.app)

    response = client.get("/api/metadata/retrieve", params={"question": "按渠道统计销售额"})

    assert response.status_code == 200
    assert response.json()["metrics"] == [{"name": "sales_amount"}]
    assert response.json()["tables"][0]["source"] == "metric_expansion"


def test_retrieve_metadata_endpoint_rejects_blank_question():
    client = TestClient(main.app)

    response = client.get("/api/metadata/retrieve", params={"question": "   "})

    assert response.status_code == 400
    assert response.json()["detail"] == "question is required"
