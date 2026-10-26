from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path
from typing import Any
from unicodedata import normalize
from urllib.parse import unquote, urlsplit


CATALOG_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "gpm_master_drawings.v1.json"
)
_WORD_PATTERN = re.compile(r"[a-z0-9]+")
_DRAWING_PATH_TYPES = frozenset({"drawing print", "partdrawing"})
_ALLOWED_DRAWING_HOST = "plm.autoliv.int"
_MINIMUM_MATCH_SCORE = 80.0
_AMBIGUITY_MARGIN = 10.0
_TITLE_QUERY_STOP_WORDS = frozenset(
    {
        "can",
        "could",
        "please",
        "you",
        "open",
        "show",
        "bring",
        "up",
        "the",
        "master",
        "drawing",
        "ogd",
        "of",
        "for",
        "me",
        "a",
        "an",
        "this",
        "want",
        "to",
    }
)


def normalize_title(value: str) -> str:
    normalized = normalize("NFKC", value).casefold()
    return " ".join(_WORD_PATTERN.findall(normalized))


def _normalize_catalog_query(value: str) -> str:
    tokens = [
        token
        for token in normalize_title(value).split()
        if token not in _TITLE_QUERY_STOP_WORDS
    ]
    if "fabric" in tokens:
        tokens = [token for token in tokens if token != "roll"]
    return " ".join(tokens)


def catalog_choice_id(record: dict[str, Any]) -> str:
    stable_value = "\0".join(
        (
            str(record.get("partNumber", "")),
            str(record.get("masterDrawingOrOgdUrl", "")),
        )
    )
    return hashlib.sha256(stable_value.encode("utf-8")).hexdigest()[:24]


def is_approved_drawing_url(value: str) -> bool:
    parsed_url = urlsplit(value)
    decoded_path = unquote(parsed_url.path).casefold()
    return (
        parsed_url.scheme == "https"
        and (parsed_url.hostname or "").casefold() == _ALLOWED_DRAWING_HOST
        and any(
            part in _DRAWING_PATH_TYPES
            for part in decoded_path.split("/")
        )
    )


def load_master_drawing_catalog(
    path: Path = CATALOG_PATH,
) -> list[dict[str, Any]]:
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "The bundled GPM master-drawing catalog could not be loaded."
        ) from error

    if catalog.get("schemaVersion") != 1:
        raise RuntimeError("The bundled GPM catalog version is unsupported.")

    records = catalog.get("records")
    if not isinstance(records, list):
        raise RuntimeError("The bundled GPM catalog has no records list.")

    for record in records:
        if not isinstance(record, dict):
            raise RuntimeError("The bundled GPM catalog contains an invalid record.")
        if not is_approved_drawing_url(
            str(record.get("masterDrawingOrOgdUrl") or "")
        ):
            raise RuntimeError(
                "The bundled GPM catalog contains a non-approved drawing URL."
            )

    return records


def _score_title_against_record(
    query: str,
    record: dict[str, Any],
) -> float:
    query_tokens = set(query.split())
    scores: list[float] = []

    standard_titles = record.get("titleAliases") or [
        record.get("plmStandardTitle", "")
    ]
    keywords = record.get("keywords", [])
    family = record.get("componentTitleFamily", "")

    for title in standard_titles:
        normalized_title = normalize_title(str(title))
        if normalized_title == query:
            scores.append(120.0)
            continue

        title_tokens = set(normalized_title.split())
        if query_tokens and query_tokens <= title_tokens:
            coverage = len(query_tokens) / max(len(title_tokens), 1)
            scores.append(96.0 + 12.0 * coverage)

    for keyword in keywords:
        normalized_keyword = normalize_title(str(keyword))
        if normalized_keyword == query:
            scores.append(115.0)
            continue

        keyword_tokens = set(normalized_keyword.split())
        if query_tokens and query_tokens <= keyword_tokens:
            coverage = len(query_tokens) / max(len(keyword_tokens), 1)
            scores.append(90.0 + 10.0 * coverage)

    normalized_family = normalize_title(str(family))
    if normalized_family == query:
        scores.append(110.0)
    else:
        family_tokens = set(normalized_family.split())
        if query_tokens and query_tokens <= family_tokens:
            coverage = len(query_tokens) / max(len(family_tokens), 1)
            scores.append(85.0 + 10.0 * coverage)

    return max(scores, default=0.0)


def _resolve_scored_records(
    scored_records: list[tuple[float, dict[str, Any]]],
) -> dict[str, Any]:
    if not scored_records:
        return {"status": "not_found", "candidates": []}

    scored_records.sort(
        key=lambda item: (
            item[0],
            str(item[1].get("plmStandardTitle", "")),
        ),
        reverse=True,
    )
    best_score = scored_records[0][0]

    best_destinations: dict[tuple[str, str], dict[str, Any]] = {}
    for score, record in scored_records:
        if best_score - score > _AMBIGUITY_MARGIN:
            break
        parsed_url = urlsplit(
            str(record.get("masterDrawingOrOgdUrl", ""))
        )
        destination = (
            str(record.get("partNumber", "")),
            parsed_url.scheme.casefold(),
            (parsed_url.hostname or "").casefold(),
            unquote(parsed_url.path),
            parsed_url.query,
        )
        best_destinations.setdefault(destination, record)

    if len(best_destinations) > 1:
        candidates = [
            {
                "choiceId": catalog_choice_id(record),
                "componentTitleFamily": record.get("componentTitleFamily"),
                "plmStandardTitle": record.get("plmStandardTitle"),
                "partNumber": record.get("partNumber"),
                "masterDrawingOrOgdLabel": record.get(
                    "masterDrawingOrOgdLabel"
                ),
                "masterDrawingOrOgdUrl": record.get(
                    "masterDrawingOrOgdUrl"
                ),
            }
            for record in best_destinations.values()
        ]
        return {"status": "ambiguous", "candidates": candidates}

    return {
        "status": "matched",
        "record": next(iter(best_destinations.values())),
        "score": best_score,
    }


def resolve_master_drawing_title_in_text(
    text: str,
    *,
    catalog_path: Path = CATALOG_PATH,
) -> dict[str, Any]:
    """Find catalog phrases explicitly present in a command/clarification."""

    query_tokens = set(normalize_title(text).split()) - _TITLE_QUERY_STOP_WORDS
    if not query_tokens:
        return {"status": "not_found", "candidates": []}

    records = load_master_drawing_catalog(catalog_path)
    explicit_titles: list[tuple[int, str]] = []
    for record in records:
        for phrase in record.get("titleAliases", []):
            phrase_words = [
                token
                for token in normalize_title(str(phrase)).split()
                if token not in _TITLE_QUERY_STOP_WORDS
                and token != "xxx"
            ]
            phrase_tokens = set(phrase_words)
            if phrase_tokens and phrase_tokens <= query_tokens:
                explicit_titles.append(
                    (len(phrase_tokens), " ".join(phrase_words))
                )

    if explicit_titles:
        _, explicit_title = max(explicit_titles)
        resolution = resolve_master_drawing_title(
            explicit_title,
            catalog_path=catalog_path,
        )
        if resolution["status"] != "not_found":
            return resolution

    normalized_query = _normalize_catalog_query(text)
    if normalized_query:
        resolution = resolve_master_drawing_title(
            normalized_query,
            catalog_path=catalog_path,
        )
        if resolution["status"] != "not_found":
            return resolution

    scored_records: list[tuple[float, dict[str, Any]]] = []
    for record in records:
        phrases = [
            *record.get("titleAliases", []),
            *record.get("keywords", []),
            record.get("componentTitleFamily", ""),
        ]
        score = 0.0
        for phrase in phrases:
            phrase_tokens = set(normalize_title(str(phrase)).split())
            phrase_tokens.discard("xxx")
            if phrase_tokens and phrase_tokens <= query_tokens:
                score = max(
                    score,
                    _score_title_against_record(
                        " ".join(phrase_tokens),
                        record,
                    ),
                )
        if score >= _MINIMUM_MATCH_SCORE:
            scored_records.append((score, record))

    return _resolve_scored_records(scored_records)


def resolve_master_drawing_choice(
    choice_id: str,
    *,
    catalog_path: Path = CATALOG_PATH,
) -> dict[str, Any] | None:
    for record in load_master_drawing_catalog(catalog_path):
        if catalog_choice_id(record) == choice_id:
            return record
    return None


def resolve_master_drawing_title(
    title: str,
    *,
    part_number: str | None = None,
    catalog_path: Path = CATALOG_PATH,
) -> dict[str, Any]:
    normalized_query = _normalize_catalog_query(title)
    if not normalized_query:
        return {"status": "not_found", "candidates": []}

    normalized_part_number = (part_number or "").strip().upper()
    scored_records: list[tuple[float, dict[str, Any]]] = []
    for record in load_master_drawing_catalog(catalog_path):
        if (
            normalized_part_number
            and str(record.get("partNumber", "")).upper()
            != normalized_part_number
        ):
            continue

        score = _score_title_against_record(normalized_query, record)
        if score >= _MINIMUM_MATCH_SCORE:
            scored_records.append((score, record))

    return _resolve_scored_records(scored_records)


