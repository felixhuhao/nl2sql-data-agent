from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from backend.app.metadata import service
from backend.app.metadata.models import DEFAULT_DATASOURCE


def normalize_evidence_text(text: object) -> str:
    return "".join(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", str(text).casefold()))


def _terms(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", str(text).casefold()))


@dataclass(frozen=True)
class EvidenceEntry:
    channel: str
    text: str
    table_name: str = ""
    column_name: str = ""
    normalized: str = ""
    terms: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SchemaEvidence:
    datasource_name: str
    _entries: tuple[EvidenceEntry, ...] = ()
    _column_values: dict[str, tuple[str, ...]] = field(default_factory=dict)
    _value_index: dict[str, tuple[str, ...]] = field(default_factory=dict)
    _column_tables: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def has_concept_evidence(self, concept: str) -> bool:
        normalized = normalize_evidence_text(concept)
        if not normalized:
            return False
        concept_terms = _terms(concept)
        return any(_entry_supports_concept(normalized, concept_terms, entry) for entry in self._entries)

    def column_values(self, column_name: str) -> tuple[str, ...]:
        return self._column_values.get(column_name, ())

    def columns_with_value(self, value: str) -> tuple[str, ...]:
        return self._value_index.get(normalize_evidence_text(value), ())

    def has_sample_value(self, column_name: str, value: str) -> bool:
        return self.value_matches(value, self.column_values(column_name))

    def value_matches(self, value: str, candidates: Iterable[object]) -> bool:
        normalized = normalize_evidence_text(value)
        if not normalized:
            return False
        return normalized in {normalize_evidence_text(candidate) for candidate in candidates}

    def has_column(self, table_name: str, column_name: str) -> bool:
        return table_name in self._column_tables.get(column_name, ())

    def unique_table_for_column(self, column_name: str) -> str | None:
        tables = self._column_tables.get(column_name, ())
        return tables[0] if len(tables) == 1 else None


ListTables = Callable[..., list[dict]]
ListColumns = Callable[..., list[dict]]
ListMetrics = Callable[..., list[dict]]
ListAliases = Callable[..., list[dict]]
ListVerified = Callable[..., list[dict]]


def build_schema_evidence(
    datasource_name: str = DEFAULT_DATASOURCE,
    *,
    list_tables: ListTables = service.list_tables,
    list_columns: ListColumns = service.list_columns,
    list_metrics: ListMetrics = service.list_metrics,
    list_aliases: ListAliases = service.list_aliases,
    list_verified_queries: ListVerified = service.list_verified_queries,
) -> SchemaEvidence:
    entries: list[EvidenceEntry] = []
    column_values: dict[str, tuple[str, ...]] = {}
    value_index: dict[str, list[str]] = {}
    column_tables: dict[str, list[str]] = {}

    tables = list_tables(datasource_name=datasource_name)
    for table in tables:
        table_name = str(table.get("table_name", ""))
        for attr in ("table_name", "display_name", "description", "domain"):
            _add_entry(entries, f"table.{attr}", table.get(attr), table_name=table_name)

    for table in tables:
        table_name = str(table.get("table_name", ""))
        for column in list_columns(table_name=table_name, datasource_name=datasource_name):
            column_name = str(column.get("column_name", ""))
            if not column_name:
                continue
            column_tables.setdefault(column_name, [])
            if table_name not in column_tables[column_name]:
                column_tables[column_name].append(table_name)
            _add_entry(entries, "column.name", column_name, table_name=table_name, column_name=column_name)
            _add_entry(
                entries,
                "column.description",
                column.get("description"),
                table_name=table_name,
                column_name=column_name,
            )
            samples = _sample_values(column.get("sample_values"))
            if samples:
                column_values[column_name] = _merge_values(column_values.get(column_name, ()), samples)
                for value in samples:
                    normalized_value = normalize_evidence_text(value)
                    if not normalized_value:
                        continue
                    _add_entry(
                        entries,
                        "column.sample_value",
                        value,
                        table_name=table_name,
                        column_name=column_name,
                    )
                    value_index.setdefault(normalized_value, [])
                    if column_name not in value_index[normalized_value]:
                        value_index[normalized_value].append(column_name)

    for metric in list_metrics(datasource_name=datasource_name):
        for attr in ("name", "label", "description", "expression"):
            _add_entry(entries, f"metric.{attr}", metric.get(attr))

    for alias in list_aliases(datasource_name=datasource_name):
        _add_entry(
            entries,
            "alias",
            alias.get("alias"),
            table_name=str(alias.get("table_name", "")),
            column_name=str(alias.get("column_name", "")),
        )

    for query in list_verified_queries(datasource_name=datasource_name):
        for attr in ("question", "sql"):
            _add_entry(entries, f"verified_query.{attr}", query.get(attr))

    return SchemaEvidence(
        datasource_name=datasource_name,
        _entries=tuple(entries),
        _column_values=column_values,
        _value_index={key: tuple(value) for key, value in value_index.items()},
        _column_tables={key: tuple(value) for key, value in column_tables.items()},
    )


def _entry_supports_concept(normalized_concept: str, concept_terms: frozenset[str], entry: EvidenceEntry) -> bool:
    if normalized_concept == entry.normalized:
        return True
    if concept_terms and concept_terms.issubset(entry.terms):
        return True
    # CJK business terms are often unsegmented and commonly two characters
    # long. Keep containment CJK-only and entry-scoped so ASCII short tokens
    # such as "id" cannot match inside values such as "paid".
    if re.fullmatch(r"[\u3400-\u9fff]+", normalized_concept) and len(normalized_concept) >= 2:
        return normalized_concept in entry.normalized
    return False


def _add_entry(
    entries: list[EvidenceEntry],
    channel: str,
    text: object,
    *,
    table_name: str = "",
    column_name: str = "",
) -> None:
    raw = str(text or "").strip()
    normalized = normalize_evidence_text(raw)
    if not normalized:
        return
    entries.append(
        EvidenceEntry(
            channel=channel,
            text=raw,
            table_name=table_name,
            column_name=column_name,
            normalized=normalized,
            terms=_terms(raw),
        )
    )


def _sample_values(raw_values: object) -> tuple[str, ...]:
    if isinstance(raw_values, str):
        try:
            parsed = json.loads(raw_values)
        except json.JSONDecodeError:
            parsed = []
    else:
        parsed = raw_values
    if not isinstance(parsed, list):
        return ()
    return tuple(dict.fromkeys(text for value in parsed if (text := str(value).strip())))


def _merge_values(existing: tuple[str, ...], new_values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*existing, *new_values)))
