from __future__ import annotations

import re


PLM_PART_NUMBER_RE = re.compile(
    r"^(?:\d{9}[A-Z]|X\d{9}[A-Z])$"
)

CAD_PART_NUMBER_RE = re.compile(
    r"^\d{7}$"
)

PLM_REVISION_RE = re.compile(
    r"^\d{2}$"
)

# X-prefixed PLM parts must use hyphenated revision form.
PLM_X_REVISION_RE = re.compile(
    r"^\d{2}-\d{2}$"
)

CAD_REVISION_RE = re.compile(
    r"^CAD\d{3}$"
)

DOWNLOAD_DOCUMENT_TYPE_ALIASES = {
    "catpart": "CATPart",
    "part": "CATPart",
    "catproduct": "CATProduct",
    "product": "CATProduct",
    "assembly": "CATProduct",
    "catdrawing": "CATDrawing",
    "drawing": "CATDrawing",
}


def normalize_download_document_type(
    document_type: str | None,
) -> str | None:
    if document_type is None:
        return None

    normalized_input = document_type.strip()

    if not normalized_input:
        return None

    normalized = DOWNLOAD_DOCUMENT_TYPE_ALIASES.get(
        normalized_input.casefold()
    )

    if normalized is None:
        raise ValueError(
            "documentType must be CATPart, CATProduct, "
            "CATDrawing, or synonyms part/product/assembly/"
            "drawing."
        )

    return normalized


def validate_download_cad_inputs(
    *,
    part_number: str | None,
    part_revision: str | None,
    part_title: str | None,
    document_type: str | None,
) -> str | None:
    normalized_part_number = (
        part_number.strip()
        if part_number is not None
        else None
    )
    normalized_part_number = (
        normalized_part_number or None
    )

    normalized_part_revision = (
        part_revision.strip()
        if part_revision is not None
        else None
    )
    normalized_part_revision = (
        normalized_part_revision or None
    )

    normalized_part_title = (
        part_title.strip()
        if part_title is not None
        else None
    )
    normalized_part_title = (
        normalized_part_title or None
    )

    has_number = normalized_part_number is not None
    has_revision = normalized_part_revision is not None
    has_title = normalized_part_title is not None

    if has_title and (has_number or has_revision):
        raise ValueError(
            "partTitle cannot be combined with partNumber or "
            "partRevision."
        )

    if not has_title:
        if has_number and not has_revision:
            raise ValueError(
                "partRevision is required when partNumber is "
                "provided."
            )

        if has_revision and not has_number:
            raise ValueError(
                "partNumber is required when partRevision is "
                "provided."
            )

        if not has_number:
            raise ValueError(
                "download_cad requires either both partNumber "
                "and partRevision, or partTitle."
            )

    if (
        normalized_part_number is not None
        and len(normalized_part_number) > 50
    ):
        raise ValueError("partNumber is too long.")

    if (
        normalized_part_revision is not None
        and len(normalized_part_revision) > 20
    ):
        raise ValueError("partRevision is too long.")

    if normalized_part_title is not None:
        if len(normalized_part_title) < 2:
            raise ValueError(
                "partTitle must be at least 2 characters."
            )

        if len(normalized_part_title) > 200:
            raise ValueError("partTitle is too long.")

    if has_number and has_revision:
        assert normalized_part_number is not None
        assert normalized_part_revision is not None

        if CAD_PART_NUMBER_RE.fullmatch(
            normalized_part_number
        ):
            if not CAD_REVISION_RE.fullmatch(
                normalized_part_revision
            ):
                raise ValueError(
                    "CAD part numbers require partRevision in "
                    "the form CAD000."
                )
        else:
            if not PLM_PART_NUMBER_RE.fullmatch(
                normalized_part_number
            ):
                raise ValueError(
                    "partNumber must be a valid PLM object "
                    "identifier (for example 674089800A or "
                    "X670830400C) or a CAD number (for "
                    "example 6740898)."
                )

            if normalized_part_number.startswith("X"):
                if not PLM_X_REVISION_RE.fullmatch(
                    normalized_part_revision
                ):
                    raise ValueError(
                        "X-prefixed PLM part numbers require "
                        "partRevision in the hyphenated form "
                        "00-01."
                    )
            elif not PLM_REVISION_RE.fullmatch(
                normalized_part_revision
            ):
                raise ValueError(
                    "PLM part numbers require partRevision in "
                    "the form 00."
                )

    return normalize_download_document_type(
        document_type
    )
