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
                    "Deterministic SQL facts:",
                    json.dumps(request.sql_facts.model_dump(), ensure_ascii=False, indent=2),
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
            "For qualified entity requests, keep the qualifier/status/filter as its own required concept; a supported base entity does not make an unsupported qualifier supported.",
            "Mark a concept supported only when the metadata provides evidence through table names, column names, labels, descriptions, aliases, metric definitions, verified queries, guidance, or sample values.",
            "Support semantics: value-derived rates, shares, counts, or trends over documented dimension/status values are supported when the column and requested value meaning are documented; an exact pre-defined metric is not required.",
            "A documented value supports only its explicit business meaning and aliases; similarity, causality, or common co-occurrence with another lifecycle/payment/fulfillment outcome is not evidence.",
            "For status/value concepts, a related documented value cannot support a requested value that is not itself named, described, or aliased in metadata.",
            "Do not require same-named columns for analytical operations such as rank correlation, YoY/MoM, moving averages, TopN, share, ratio, or compatible arithmetic.",
            "Do not invent synonym rules. If the metadata does not support the requested business concept, mark supported=false.",
            "Return one JSON object and nothing else.",
            "Schema:",
            '{ "concepts": [',
            '  { "concept_id": "c1", "concept": "删除率", "concept_type": "metric", "supported": false, "evidence": [], "explanation": "No deleted/deletion concept appears in metadata." }',
            "] }",
        ]
    )


def _grounding_check_system_prompt() -> str:
    return "\n".join(
        [
            "You are a semantic grounding critic for a governed NL2SQL data agent.",
            "You receive unsupported required concepts already extracted from the user's question, deterministic SQL facts, and one candidate SQL statement.",
            "Reference concepts by concept_id from Stage A and repeat the Stage-A concept_type. Do not rename or reinterpret the required concept.",
            "failure_kind=substituted when SQL maps a concept_id to a different concrete column, value, metric, or expression.",
            "failure_kind=omitted when SQL contains no mapping/filter/calculation for that concept_id.",
            "Use deterministic SQL facts as observations; do not override them.",
            "Do not flag analytical operations such as rank correlation, YoY/MoM, moving averages, TopN, share, ratio, or compatible arithmetic.",
            "Return ok=true only when there are no unsupported-concept substitutions or omissions.",
            "Return one JSON object and nothing else.",
            "Schema:",
            '{ "ok": false, "issues": [',
            '  { "concept_id": "c1", "concept_type": "metric", "failure_kind": "substituted", "sql_mapping": "order_status = \'refunded\'", "supported": false, "explanation": "The SQL maps deletion rate to refunded status without schema evidence." }',
            "] }",
        ]
    )
