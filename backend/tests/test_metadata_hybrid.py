from backend.app.metadata import hybrid
from backend.app.metadata.vector.searcher import VectorRetrievalResult
from backend.app.metadata.vector.store import VectorSearchHit


def test_hybrid_merge_adds_vector_metric_and_sources(monkeypatch):
    monkeypatch.setattr(hybrid, "search_values", lambda question: [])
    monkeypatch.setattr(
        hybrid,
        "retrieve_vector_assets",
        lambda question: VectorRetrievalResult(
            vector_used=True,
            index_status="ready",
            hits={
                "metrics": [
                    VectorSearchHit(
                        asset_type="metric",
                        asset_id="sales_amount",
                        text="销售额",
                        distance=0.1,
                        score=0.9,
                        metadata={
                            "name": "sales_amount",
                            "label": "销售额",
                            "expression": "SUM(fact_orders.payment_amount)",
                        },
                    )
                ]
            },
        ),
    )

    result = hybrid.hybrid_merge(
        _empty_rule_result(),
        "营收总额",
        table_limit=5,
        column_limit=20,
        metric_limit=5,
        verified_query_limit=3,
    )

    assert result["fallback_used"] is False
    assert result["metrics"][0]["name"] == "sales_amount"
    assert result["retrieval_meta"]["vector_used"] is True
    assert result["retrieval_meta"]["index_status"] == "ready"
    assert result["retrieval_meta"]["sources"]["metric:sales_amount"] == ["vector:0.90"]


def test_hybrid_merge_preserves_rule_hits_and_adds_vector_source(monkeypatch):
    monkeypatch.setattr(hybrid, "search_values", lambda question: [])
    monkeypatch.setattr(
        hybrid,
        "retrieve_vector_assets",
        lambda question: VectorRetrievalResult(
            vector_used=True,
            index_status="ready",
            hits={
                "tables": [
                    VectorSearchHit(
                        asset_type="table",
                        asset_id="fact_orders",
                        text="订单",
                        distance=0.2,
                        score=0.8,
                        metadata={"table_name": "fact_orders"},
                    )
                ]
            },
        ),
    )
    rule_result = {
        **_empty_rule_result(),
        "tables": [
            {
                "table_name": "fact_orders",
                "source": "direct_match",
                "score": 10,
                "reasons": ["table_name"],
            }
        ],
    }

    result = hybrid.hybrid_merge(
        rule_result,
        "订单",
        table_limit=5,
        column_limit=20,
        metric_limit=5,
        verified_query_limit=3,
    )

    assert result["tables"][0]["table_name"] == "fact_orders"
    assert result["tables"][0]["source"] == "direct_match"
    assert result["retrieval_meta"]["sources"]["table:fact_orders"] == [
        "rule:table_name",
        "vector:0.80",
    ]


def test_hybrid_merge_injects_value_hits(monkeypatch):
    monkeypatch.setattr(
        hybrid,
        "retrieve_vector_assets",
        lambda question: VectorRetrievalResult(vector_used=True, index_status="ready", hits={}),
    )
    monkeypatch.setattr(
        hybrid,
        "search_values",
        lambda question: [
            hybrid.ValueHit(
                table_name="dim_regions",
                column_name="region_group",
                matched_value="华东",
                source="exact",
                score=1.0,
            )
        ],
    )

    result = hybrid.hybrid_merge(
        _empty_rule_result(),
        "华东地区销售额",
        table_limit=5,
        column_limit=20,
        metric_limit=5,
        verified_query_limit=3,
    )

    assert result["tables"][0]["table_name"] == "dim_regions"
    assert result["columns"][0]["table_name"] == "dim_regions"
    assert result["columns"][0]["column_name"] == "region_group"
    assert result["retrieval_meta"]["value_hits"] == [
        {
            "table_name": "dim_regions",
            "column_name": "region_group",
            "matched_value": "华东",
            "source": "exact",
            "score": 1.0,
        }
    ]
    assert result["retrieval_meta"]["sources"]["column:dim_regions.region_group"] == ["value:华东"]


def test_hybrid_merge_passes_existing_query_vector_to_value_recall(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        hybrid,
        "retrieve_vector_assets",
        lambda question: VectorRetrievalResult(
            vector_used=True,
            index_status="ready",
            hits={},
            query_vector=[0.1, 0.2, 0.3],
        ),
    )

    def fake_search_values(question, *, query_vector=None):
        captured["question"] = question
        captured["query_vector"] = query_vector
        return []

    monkeypatch.setattr(hybrid, "search_values", fake_search_values)

    hybrid.hybrid_merge(
        _empty_rule_result(),
        "华东地区销售额",
        table_limit=5,
        column_limit=20,
        metric_limit=5,
        verified_query_limit=3,
    )

    assert captured == {
        "question": "华东地区销售额",
        "query_vector": [0.1, 0.2, 0.3],
    }


def test_hybrid_merge_skips_value_recall_errors(monkeypatch):
    monkeypatch.setattr(
        hybrid,
        "retrieve_vector_assets",
        lambda question: VectorRetrievalResult(vector_used=True, index_status="ready", hits={}),
    )
    monkeypatch.setattr(
        hybrid,
        "search_values",
        lambda question: (_ for _ in ()).throw(RuntimeError("value recall failed")),
    )

    result = hybrid.hybrid_merge(
        _empty_rule_result(),
        "华东地区销售额",
        table_limit=5,
        column_limit=20,
        metric_limit=5,
        verified_query_limit=3,
    )

    assert result["tables"] == []
    assert result["columns"] == []
    assert result["retrieval_meta"]["value_hits"] == []


def _empty_rule_result():
    return {
        "question": "营收总额",
        "normalized_question": "营收总额",
        "fallback_used": False,
        "tables": [],
        "columns": [],
        "metrics": [],
        "verified_queries": [],
    }
