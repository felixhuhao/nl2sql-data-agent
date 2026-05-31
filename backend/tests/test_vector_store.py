from pathlib import Path

import pytest

from backend.app.metadata.vector import store


def test_ensure_tables_creates_all_vector_tables(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(store, "_vector_schema", lambda dimension: f"vector:{dimension}")
    monkeypatch.setattr(store, "_metadata_schema", lambda: "metadata")

    vector_store = store.LanceVectorStore(db_path=Path("unused"), db=db)
    vector_store.ensure_tables(embedding_dimension=3)

    assert set(db.tables) == set(store.ALL_TABLE_NAMES)
    assert db.tables["metric_vectors"].schema == "vector:3"
    assert db.tables[store.METADATA_TABLE_NAME].schema == "metadata"


def test_upsert_search_list_values_and_delete(monkeypatch):
    db = FakeDB.with_tables(["metric_vectors", "value_vectors"])
    monkeypatch.setattr(store, "_records_to_arrow_table", lambda records: records)
    vector_store = store.LanceVectorStore(db_path=Path("unused"), db=db)

    vector_store.upsert_rows(
        [
            store.VectorRow(
                table_name="metric_vectors",
                asset_type="metric",
                asset_id="sales_amount",
                text="销售额",
                vector=[0.1, 0.2, 0.3],
                metadata={"label": "销售额"},
            )
        ]
    )

    hits = vector_store.search("metric_vectors", [0.1, 0.2, 0.3], limit=1, where="asset_type = 'metric'")
    assert hits == [
        store.VectorSearchHit(
            asset_type="metric",
            asset_id="sales_amount",
            text="销售额",
            distance=0.25,
            score=0.8,
            metadata={"label": "销售额"},
        )
    ]
    assert db.tables["metric_vectors"].queries[0].where_clause == "asset_type = 'metric'"

    values = vector_store.list_values(limit=5)
    assert values == []
    assert db.tables["value_vectors"].queries[0].vector is None
    assert db.tables["value_vectors"].queries[0].limit_value == 5

    vector_store.delete_by_ids("metric_vectors", ["metric:sales_amount", "metric:aov's"])
    assert db.tables["metric_vectors"].deleted_predicates == [
        "id IN ('metric:sales_amount', 'metric:aov''s')"
    ]


def test_clear_vector_tables_deletes_only_vector_tables():
    db = FakeDB.with_tables(store.ALL_TABLE_NAMES)
    vector_store = store.LanceVectorStore(db_path=Path("unused"), db=db)

    vector_store.clear_vector_tables()

    for table_name in store.VECTOR_TABLE_NAMES:
        assert db.tables[table_name].deleted_predicates == ["id IS NOT NULL"]
    assert db.tables[store.METADATA_TABLE_NAME].deleted_predicates == []


def test_metadata_round_trip_and_ready_status(monkeypatch):
    db = FakeDB.with_tables(store.ALL_TABLE_NAMES)
    monkeypatch.setattr(store, "_records_to_arrow_table", lambda records: records)
    vector_store = store.LanceVectorStore(db_path=Path("unused"), db=db)
    metadata = store.VectorIndexMetadata(
        embedding_model="/models/BAAI/bge-m3",
        embedding_dimension=1024,
        built_at="2026-05-31T10:00:00Z",
        asset_counts={"metric": 3},
    )

    vector_store.write_metadata(metadata)

    assert vector_store.read_metadata() == metadata
    assert vector_store.status(
        expected_model="/models/BAAI/bge-m3",
        expected_dimension=1024,
    ) == store.VectorIndexStatus(
        status="ready",
        embedding_model="/models/BAAI/bge-m3",
        embedding_dimension=1024,
        built_at="2026-05-31T10:00:00Z",
        asset_counts={"metric": 3},
    )


def test_status_reports_missing_and_stale(monkeypatch):
    db = FakeDB.with_tables(store.ALL_TABLE_NAMES)
    monkeypatch.setattr(store, "_records_to_arrow_table", lambda records: records)
    vector_store = store.LanceVectorStore(db_path=Path("unused"), db=db)

    assert vector_store.status().status == "missing"

    vector_store.write_metadata(
        store.VectorIndexMetadata(
            embedding_model="model-a",
            embedding_dimension=3,
            built_at="2026-05-31T10:00:00Z",
            asset_counts={},
        )
    )

    stale_status = vector_store.status(expected_model="model-b", expected_dimension=3)
    assert stale_status.status == "stale"
    assert stale_status.stale_reason == "Embedding model mismatch."

    missing_store = store.LanceVectorStore(db_path=Path("unused"), db=FakeDB())
    assert missing_store.status().status == "missing"
    assert "Missing LanceDB tables" in (missing_store.status().stale_reason or "")


def test_invalid_table_name_is_rejected():
    vector_store = store.LanceVectorStore(db_path=Path("unused"), db=FakeDB())

    with pytest.raises(ValueError, match="Unknown vector table"):
        vector_store.search("bad_table", [1.0])


class FakeDB:
    def __init__(self) -> None:
        self.tables = {}

    @classmethod
    def with_tables(cls, table_names) -> "FakeDB":
        db = cls()
        for table_name in table_names:
            db.tables[table_name] = FakeTable(table_name)
        return db

    def table_names(self):
        return list(self.tables)

    def create_table(self, name, schema=None, exist_ok=False):
        if name in self.tables and not exist_ok:
            raise ValueError(f"table exists: {name}")
        self.tables.setdefault(name, FakeTable(name, schema=schema))
        self.tables[name].schema = schema
        return self.tables[name]

    def open_table(self, name):
        return self.tables[name]


class FakeTable:
    def __init__(self, name, schema=None) -> None:
        self.name = name
        self.schema = schema
        self.records = {}
        self.deleted_predicates = []
        self.queries = []

    def merge_insert(self, key):
        return FakeMerge(self, key)

    def delete(self, predicate):
        self.deleted_predicates.append(predicate)

    def search(self, vector=None):
        query = FakeQuery(self, vector)
        self.queries.append(query)
        return query


class FakeMerge:
    def __init__(self, table, key) -> None:
        self.table = table
        self.key = key

    def when_matched_update_all(self):
        return self

    def when_not_matched_insert_all(self):
        return self

    def execute(self, records):
        for record in records:
            self.table.records[record[self.key]] = dict(record)


class FakeQuery:
    def __init__(self, table, vector) -> None:
        self.table = table
        self.vector = vector
        self.where_clause = None
        self.limit_value = None

    def where(self, where_clause):
        self.where_clause = where_clause
        return self

    def limit(self, limit):
        self.limit_value = limit
        return self

    def to_list(self):
        records = list(self.table.records.values())
        if self.limit_value is not None:
            records = records[: self.limit_value]
        return [dict(record, _distance=0.25) for record in records]
