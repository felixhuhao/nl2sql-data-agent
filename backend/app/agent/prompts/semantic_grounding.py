from __future__ import annotations

import json

from backend.app.agent.semantic_grounding import ConceptExtractionRequest, GroundingCheckRequest


def build_concept_extraction_messages(request: ConceptExtractionRequest) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": _concept_extraction_system_prompt(),
        },
        {
            "role": "user",
            "content": "\n".join(
                [
                    "Full datasource metadata:",
                    request.full_schema_context,
                    "",
                    "Question:",
                    request.question,
                    "",
                    "Output JSON only.",
                ]
            ),
        },
    ]


def build_grounding_check_messages(request: GroundingCheckRequest) -> list[dict[str, str]]:
    concepts = [concept.model_dump() for concept in request.concepts if not concept.supported]
    return [
        {
            "role": "system",
            "content": _grounding_check_system_prompt(),
        },
        {
            "role": "user",
            "content": "\n".join(
                [
                    "Question:",
                    request.question,
                    "",
                    "Unsupported required concepts from the schema critic:",
                    json.dumps(concepts, ensure_ascii=False, indent=2),
                    "",
                    "Candidate SQL:",
                    request.sql,
                    "",
                    "Output JSON only.",
                ]
            ),
        },
    ]


def _concept_extraction_system_prompt() -> str:
    return "\n".join(
        [
            "You are a semantic grounding critic for a governed NL2SQL data agent.",
            "Your job is to read the user's question and the full datasource metadata.",
            "Start from the question, not from any SQL.",
            "List only business entities, filters, status values, and named metrics that the question requires.",
            "Preserve the user's requested concept type: a request for records/entities/filters stays a records/entities/filter concept; do not rewrite it as a rate or metric.",
            "Mark a concept supported only when the metadata provides evidence through table names, column names, labels, descriptions, aliases, metric definitions, verified queries, guidance, or sample values.",
            "For value-derived metrics such as a rate, share, count, or trend over a documented dimension/status value, mark the concept supported when both the column and the requested value meaning are documented; a pre-defined Metric Definition with the exact metric name is not required.",
            "Do not require same-named columns for analytical operations such as rank correlation, YoY/MoM, moving averages, TopN, share, ratio, or compatible arithmetic.",
            "Do not invent synonym rules. If the metadata does not support the requested business concept, mark supported=false.",
            "Return one JSON object and nothing else.",
            "Schema:",
            '{ "concepts": [',
            '  { "concept": "删除率", "concept_type": "metric", "supported": false, "evidence": [], "explanation": "No deleted/deletion concept appears in metadata." }',
            "] }",
        ]
    )


def _grounding_check_system_prompt() -> str:
    return "\n".join(
        [
            "You are a semantic grounding critic for a governed NL2SQL data agent.",
            "You receive unsupported required concepts already extracted from the user's question, plus one candidate SQL statement.",
            "For each unsupported concept, decide whether the SQL substituted a proxy for it or omitted it entirely.",
            "Flag substitution when the SQL answers the unsupported concept with another available column, value, metric, or expression.",
            "If the unsupported concept is a filter/entity and the SQL filters to a different status or value, classify that as substituted, not omitted.",
            "Flag omission when the question requires the concept but the SQL contains no mapping/filter/calculation for it.",
            "Do not flag a rate, share, count, or trend calculation merely because there is no pre-defined metric, as long as the required dimension/status value was marked supported by the extraction stage.",
            "Do not flag analytical operations such as rank correlation, YoY/MoM, moving averages, TopN, share, ratio, or compatible arithmetic.",
            "Return ok=true only when there are no unsupported-concept substitutions or omissions.",
            "Return one JSON object and nothing else.",
            "Schema:",
            '{ "ok": false, "issues": [',
            '  { "concept": "删除率", "failure_kind": "substituted", "sql_mapping": "order_status = \'refunded\'", "supported": false, "explanation": "The SQL maps deletion rate to refunded status without schema evidence." }',
            "] }",
        ]
    )
