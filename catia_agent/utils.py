from __future__ import annotations

import re
from pathlib import PureWindowsPath


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

DEFAULT_DOWNLOAD_SAVE_PATH = r"C:\Temp"

START_MODEL_TYPE_ALIASES = {
    "sab": "SAB",
    "side airbag": "SAB",
    "airbag": "SAB",
    "du": "DU",
    "driver unit": "DU",
    "steering wheel": "DU",
    "seatbelt": "SEATBELT",
    "buckle": "SEATBELT",
    "ic": "IC",
    "inflatable curtain": "IC",
    "curtain airbag": "IC",
}

OUTSYSTEMS_ID_RE = re.compile(
    r"^[A-Za-z0-9._:-]+$"
)

START_MODEL_PART_NUMBER_RE = re.compile(
    r"^(?:\d{9,10}[A-Z]|X\d{9,10}[A-Z])$"
)


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
    save_path: str | None,
) -> tuple[str | None, str]:
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

    normalized_document_type = normalize_download_document_type(
        document_type
    )

    normalized_save_path = (
        save_path.strip()
        if save_path is not None
        else ""
    )

    if not normalized_save_path:
        normalized_save_path = DEFAULT_DOWNLOAD_SAVE_PATH

    save_path_object = PureWindowsPath(
        normalized_save_path
    )

    if not save_path_object.is_absolute():
        raise ValueError(
            "savePath must be an absolute Windows path."
        )

    return normalized_document_type, str(save_path_object)


def normalize_start_model_type(
    model_type: str,
) -> str:
    normalized_input = " ".join(
        model_type.strip().casefold().split()
    )

    if not normalized_input:
        raise ValueError(
            "modelType is required for start_model."
        )

    normalized = START_MODEL_TYPE_ALIASES.get(
        normalized_input
    )

    if normalized is None:
        raise ValueError(
            "modelType must be SAB, DU, SEATBELT, or supported "
            "aliases (side airbag, steering wheel, buckle)."
        )

    return normalized


def validate_start_model_inputs(
    *,
    outsystems_id: str | None,
    model_type: str,
    part_number: str | None,
    afis_number: str | None,
    custom_name: str | None,
    part_revision: str | None,
    save_path: str | None,
) -> tuple[
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    str,
    str,
]:
    normalized_model_type = normalize_start_model_type(
        model_type
    )

    normalized_outsystems_id = (
        outsystems_id.strip()
        if outsystems_id is not None
        else None
    )
    normalized_outsystems_id = (
        normalized_outsystems_id or None
    )

    normalized_part_number = (
        part_number.strip()
        if part_number is not None
        else None
    )
    normalized_part_number = (
        normalized_part_number or None
    )
    
    normalized_afis_number = (
        afis_number.strip()
        if afis_number is not None
        else None
    )
    
    normalized_afis_number = (
        normalized_afis_number or None
    )
    
    normalized_custom_name = (
        custom_name.strip()
        if custom_name is not None
        else None
    )
    
    normalized_custom_name = (
        normalized_custom_name or None
    )

    normalized_part_revision = (
        part_revision.strip()
        if part_revision is not None
        else None
    )
    normalized_part_revision = (
        normalized_part_revision or None
    )

    if normalized_model_type == "IC":
        raise ValueError(
            "Currently IC start model is not supported."
        )

    normalized_save_path = (
        save_path.strip()
        if save_path is not None
        else ""
    )

    if not normalized_save_path:
        normalized_save_path = DEFAULT_DOWNLOAD_SAVE_PATH

    save_path_object = PureWindowsPath(
        normalized_save_path
    )

    if not save_path_object.is_absolute():
        raise ValueError(
            "savePath must be an absolute Windows path."
        )

    if normalized_model_type in {"SAB", "DU"}:
        if (
            normalized_part_number is not None
            or normalized_part_revision is not None
        ):
            raise ValueError(
                "partNumber and partRevision are not used for this start model."
            )

        if normalized_outsystems_id is None:
            raise ValueError(
                "outsystemsId is required for SAB and DU start models."
            )

        if normalized_afis_number is None:
            raise ValueError(
                "afisNumber is required for SAB and DU start models."
            )

        if len(normalized_outsystems_id) > 50:
            raise ValueError("outsystemsId is too long.")

        if len(normalized_afis_number) > 50:
            raise ValueError("afisNumber is too long.")

        if (
            normalized_custom_name is not None
            and len(normalized_custom_name) > 200
        ):
            raise ValueError("customName is too long.")

        if not OUTSYSTEMS_ID_RE.fullmatch(
            normalized_outsystems_id
        ):
            raise ValueError(
                "outsystemsId contains unsupported characters."
            )

        return (
            normalized_outsystems_id,
            None,
            None,
            normalized_afis_number,
            normalized_custom_name,
            normalized_model_type,
            str(save_path_object),
        )

    if normalized_model_type == "SEATBELT":
        if normalized_outsystems_id is not None:
            raise ValueError(
                "outsystemsId is not used for SEATBELT start models."
            )

        if normalized_afis_number is not None:
            raise ValueError(
                "afisNumber is not used for SEATBELT start models."
            )

        if normalized_custom_name is not None:
            raise ValueError(
                "customName is not used for SEATBELT start models."
            )

        if (
            normalized_part_number is None
            or normalized_part_revision is None
        ):
            raise ValueError(
                "partNumber and partRevision are required for SEATBELT start models."
            )

        if len(normalized_part_number) > 50:
            raise ValueError("partNumber is too long.")

        if len(normalized_part_revision) > 20:
            raise ValueError("partRevision is too long.")

        if not START_MODEL_PART_NUMBER_RE.fullmatch(
            normalized_part_number
        ):
            raise ValueError(
                "partNumber must be a valid start-model identifier (for example 674089800A, 6687874100A, or X6565654500A)."
            )

        if normalized_part_number.startswith("X"):
            if not PLM_X_REVISION_RE.fullmatch(
                normalized_part_revision
            ):
                raise ValueError(
                    "X-prefixed start-model part numbers require partRevision in the hyphenated form 00-01."
                )
        elif not PLM_REVISION_RE.fullmatch(
            normalized_part_revision
        ):
            raise ValueError(
                "SEATBELT start models require partRevision in the form 00."
            )

        return (
            None,
            normalized_part_number,
            normalized_part_revision,
            None,
            None,
            normalized_model_type,
            str(save_path_object),
        )

    raise ValueError(
        "Unsupported start model type."
    )
