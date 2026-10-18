from __future__ import annotations

from catia_agent.gpm_catalog import (
    is_approved_drawing_url,
    load_master_drawing_catalog,
    resolve_master_drawing_title,
)


def test_bundled_catalog_contains_only_versioned_drawing_records() -> None:
    records = load_master_drawing_catalog()

    assert len(records) == 35
    assert all(
        is_approved_drawing_url(record["masterDrawingOrOgdUrl"])
        for record in records
    )


def test_backshell_title_resolves_to_drawing_number_and_hyperlink() -> None:
    result = resolve_master_drawing_title("backshell")

    assert result["status"] == "matched"
    assert result["record"]["plmStandardTitle"] == "BACKSHELL, XXX"
    assert result["record"]["partNumber"] == "6481212"
    assert result["record"]["masterDrawingOrOgdLabel"] == "6481212"
    assert result["record"]["masterDrawingOrOgdUrl"].endswith(
        "/Drawing%20Print/6481212/*"
    )


def test_back_shell_lid_title_selects_the_more_specific_record() -> None:
    result = resolve_master_drawing_title("backshell lid")

    assert result["status"] == "matched"
    assert result["record"]["plmStandardTitle"] == "BACKSHELL, LID, XXX"
    assert result["record"]["partNumber"] == "6481333"


def test_title_with_multiple_distinct_drawing_links_is_ambiguous() -> None:
    result = resolve_master_drawing_title(
        "spring dab retention in sw asw8"
    )

    assert result["status"] == "ambiguous"
    assert {
        candidate["partNumber"]
        for candidate in result["candidates"]
    } == {"6446798", "6471900", "6557983"}


def test_unknown_title_does_not_guess_a_drawing() -> None:
    result = resolve_master_drawing_title("unknown component name")

    assert result == {"status": "not_found", "candidates": []}


def test_drawing_url_allowlist_rejects_other_document_links() -> None:
    assert is_approved_drawing_url(
        "https://plm.autoliv.int/enovia/goto/download/latestreleased/"
        "Drawing%20Print/6481212/*"
    )
    assert not is_approved_drawing_url(
        "https://plm.autoliv.int/enovia/goto/download/latestreleased/"
        "alvPartSpecification/6481212/*"
    )
    assert not is_approved_drawing_url(
        "https://example.com/enovia/goto/download/latestreleased/"
        "Drawing%20Print/6481212/*"
    )