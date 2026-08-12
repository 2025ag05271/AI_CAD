from __future__ import annotations

import re


# -------------------------------------------------------------------------
# Deterministic request-shape classification
#
# This replaces a previous spaCy dependency-parse classifier. spaCy added
# a ~30MB model download, non-trivial per-process load time, and its
# statistical POS tagging was not meaningfully more reliable than a
# lexical heuristic for this narrow job. It is not the ask-vs-agent
# decision maker: that decision is made exclusively by the model, via
# tool selection between route_to_general_chat and a CATIA tool (see
# _build_system_prompt in planner.py). This classifier only supplies a
# cheap, conservative signal for whether the follow-up planning loop in
# record_action_result_node can be skipped after the first action. Since
# the Azure-hosted model is materially more reliable at obeying the
# goal-delta follow-up prompting than the earlier small local model, this
# classifier can afford to be simple and err toward "follow up required"
# whenever it is unsure; the duplicate-action guard and silent-model
# recovery in plan_action_node remain the deterministic safety net
# regardless of classifier or model quality.
# -------------------------------------------------------------------------

_CATIA_VERB_PATTERN = re.compile(
    r"^(?:open|clos\w*|creat\w*|download\w*|start\w*|get\w*|list\w*)",
    re.IGNORECASE,
)

_SEQUENCE_CONNECTOR_SPLIT_PATTERN = re.compile(
    r"\b(?:then|after\s+that|afterwards|followed\s+by|next)\b|;|\band\b",
    re.IGNORECASE,
)

_LEADING_FILLER_PATTERN = re.compile(
    r"""^(?:
        [\s,.:;!-]+
        |cool\b
        |okay\b|ok\b
        |please\b
        |kindly\b
        |just\b
        |now\b
        |well\b
        |alright\b
        |so\b
        |(?:can|could|would|will)\s+(?:you|i|we)?\s*(?:please\s+)?
        |i\s+(?:want|need|would\s+like)\s+to\s+
        |i'?d\s+like\s+to\s+
        |let'?s\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def _strip_leading_filler(
    segment: str,
) -> str:
    previous_segment = None
    current_segment = segment.strip()

    while previous_segment != current_segment:
        previous_segment = current_segment
        current_segment = _LEADING_FILLER_PATTERN.sub(
            "",
            current_segment,
            count=1,
        ).strip()

    return current_segment


def _split_into_clauses(
    user_message: str,
) -> list[str]:
    segments = _SEQUENCE_CONNECTOR_SPLIT_PATTERN.split(
        user_message
    )

    return [
        segment
        for segment in segments
        if segment and segment.strip()
    ]


def _clause_starts_with_catia_verb(
    clause: str,
) -> bool:
    cleaned_clause = _strip_leading_filler(clause)

    return (
        _CATIA_VERB_PATTERN.match(cleaned_clause)
        is not None
    )


def _extract_predicate_count_signal(
    signals: list[str],
) -> int | None:
    prefix = "multiple_action_verbs:"

    for signal in signals:
        if not signal.startswith(prefix):
            continue

        try:
            return int(
                signal.split(":", maxsplit=1)[1]
            )

        except ValueError:
            return None

    return None


def _classify_planning_mode(
    user_message: str,
) -> tuple[str, str, list[str], int | None]:
    normalized_message = user_message.strip()

    if not normalized_message:
        return (
            "follow_up_required",
            "low",
            ["empty_message"],
            None,
        )

    clauses = _split_into_clauses(normalized_message)
    has_connector = len(clauses) > 1

    matched_action_count = sum(
        1
        for clause in clauses
        if _clause_starts_with_catia_verb(clause)
    )

    if matched_action_count == 0:
        return (
            "follow_up_required",
            "low",
            ["no_catia_verbs"],
            None,
        )

    if matched_action_count > 1:
        return (
            "follow_up_required",
            "high",
            [
                f"expected_catia_actions:{matched_action_count}",
                f"multiple_action_verbs:{matched_action_count}",
            ],
            matched_action_count,
        )

    if has_connector:
        return (
            "single_action_fast_path",
            "medium",
            [
                "expected_catia_actions:1",
                "mixed_predicates_single_catia_intent",
            ],
            1,
        )

    return (
        "single_action_fast_path",
        "high",
        [
            "expected_catia_actions:1",
            "single_action_fast_path",
        ],
        1,
    )
