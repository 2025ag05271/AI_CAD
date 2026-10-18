from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from unicodedata import category, normalize
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "gpm_master_drawings.v1.json"
ALLOWED_HOST = "plm.autoliv.int"
DRAWING_PATH_TYPES = frozenset({"drawing print", "partdrawing"})
PART_NUMBER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._-]{2,49}$")


@dataclass(slots=True)
class Cell:
    text: list[str] = field(default_factory=list)
    href: str | None = None
    rowspan: int = 1
    colspan: int = 1

    @property
    def value(self) -> str:
        text = normalize("NFKC", "".join(self.text))
        text = "".join(
            character
            for character in text
            if character in "\n\t" or category(character) != "Cf"
        )
        return " ".join(text.replace("\xa0", " ").split()).strip()


class SpreadsheetTableParser(HTMLParser):
    """Read the first HTML table from Excel's exported workbook sheet."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[Cell]] = []
        self.current_row: list[Cell] | None = None
        self.current_cell: Cell | None = None
        self.in_target_table = False
        self.seen_table = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "table" and not self.seen_table:
            self.in_target_table = True
            self.seen_table = True
        elif self.in_target_table and tag == "tr":
            self.current_row = []
        elif self.in_target_table and tag == "td" and self.current_row is not None:
            self.current_cell = Cell(
                href=None,
                rowspan=_positive_int(attributes.get("rowspan")),
                colspan=_positive_int(attributes.get("colspan")),
            )
            self.current_row.append(self.current_cell)
        elif self.in_target_table and tag == "a" and self.current_cell is not None:
            self.current_cell.href = attributes.get("href")

    def handle_data(self, data: str) -> None:
        if self.current_cell is not None:
            self.current_cell.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td":
            self.current_cell = None
        elif tag == "tr" and self.current_row is not None:
            self.rows.append(self.current_row)
            self.current_row = None
        elif tag == "table" and self.in_target_table:
            self.in_target_table = False


def _positive_int(value: str | None) -> int:
    try:
        return max(1, int(value or "1"))
    except ValueError:
        return 1


def _expand_rowspans(
    rows: list[list[Cell]],
    column_count: int,
) -> Iterator[list[Cell | None]]:
    active_spans: dict[int, tuple[Cell, int]] = {}

    for row in rows:
        grid: dict[int, Cell] = {}
        next_spans: dict[int, tuple[Cell, int]] = {}

        for column, (cell, remaining_rows) in active_spans.items():
            grid[column] = cell
            if remaining_rows > 1:
                next_spans[column] = (cell, remaining_rows - 1)

        column = 0
        for cell in row:
            while any(column + offset in grid for offset in range(cell.colspan)):
                column += 1

            for offset in range(cell.colspan):
                target_column = column + offset
                grid[target_column] = cell
                if cell.rowspan > 1:
                    next_spans[target_column] = (cell, cell.rowspan - 1)

            column += cell.colspan

        active_spans = next_spans
        yield [grid.get(index) for index in range(column_count)]


def _split_values(value: str, separators: str) -> list[str]:
    return [part.strip() for part in re.split(separators, value) if part.strip()]


def _extract_part_number(url: str) -> str | None:
    path_parts = [part for part in unquote(urlsplit(url).path).split("/") if part]
    normalized_parts = [part.casefold() for part in path_parts]

    for index, part in enumerate(normalized_parts[:-1]):
        if part not in DRAWING_PATH_TYPES:
            continue
        candidate = path_parts[index + 1].strip().upper()
        if PART_NUMBER_PATTERN.fullmatch(candidate):
            return candidate

    return None


def _valid_drawing_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlsplit(url)
    if parsed.scheme != "https" or (parsed.hostname or "").casefold() != ALLOWED_HOST:
        return False
    return any(
        path_part.casefold() in DRAWING_PATH_TYPES
        for path_part in unquote(parsed.path).split("/")
    )


def convert_workbook(source_path: Path) -> dict[str, Any]:
    source_path = source_path.resolve()
    if source_path.suffix.casefold() in {".htm", ".html"}:
        sheet_path = source_path
    else:
        sheet_path = source_path.parent / f"{source_path.stem}_files" / "sheet001.htm"

    if not sheet_path.is_file():
        raise FileNotFoundError(
            "Expected the Excel-exported sheet at "
            f"'{sheet_path}'. For a native binary .xls/.xlsx, export the sheet "
            "as HTML or add a workbook reader."
        )

    parser = SpreadsheetTableParser()
    parser.feed(sheet_path.read_text(encoding="utf-8", errors="replace"))
    if not parser.rows:
        raise ValueError("The workbook sheet contained no HTML table rows.")

    headers = [cell.value for cell in parser.rows[0]]
    required_headers = {
        "Component_Title_Family",
        "PLM_Standard_Title",
        "MasterDrawingOrOGD",
        "Keywords",
    }
    missing_headers = required_headers - set(headers)
    if missing_headers:
        raise ValueError(
            "The sheet is missing required columns: "
            + ", ".join(sorted(missing_headers))
        )

    header_indices = {header: index for index, header in enumerate(headers)}
    records: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    rejected_non_drawing_links = 0
    rows_with_title_and_link = 0

    for expanded_row in _expand_rowspans(
        parser.rows[1:],
        len(headers),
    ):
        def value(header: str) -> str:
            cell = expanded_row[header_indices[header]]
            return cell.value if cell is not None else ""

        master_cell = expanded_row[header_indices["MasterDrawingOrOGD"]]
        standard_title = value("PLM_Standard_Title")
        if not standard_title or master_cell is None:
            continue

        if not _valid_drawing_url(master_cell.href):
            if master_cell.href:
                rejected_non_drawing_links += 1
            continue

        rows_with_title_and_link += 1
        url = master_cell.href or ""
        part_number = _extract_part_number(url)
        if part_number is None:
            rejected_non_drawing_links += 1
            continue

        title_family = value("Component_Title_Family")
        keywords = _split_values(value("Keywords"), r"[,;\n]+")
        aliases = _split_values(standard_title, r"[;\n]+")
        record = {
            "componentTitleFamily": title_family,
            "plmStandardTitle": standard_title,
            "titleAliases": aliases,
            "keywords": keywords,
            "partNumber": part_number,
            "masterDrawingOrOgdLabel": master_cell.value,
            "masterDrawingOrOgdUrl": url,
        }
        identity = (
            normalize("NFKC", title_family).casefold(),
            normalize("NFKC", standard_title).casefold(),
            normalize("NFKC", ",".join(keywords)).casefold(),
            url,
        )
        records.setdefault(identity, record)

    workbook_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    sheet_hash = hashlib.sha256(sheet_path.read_bytes()).hexdigest()
    result = {
        "schemaVersion": 1,
        "source": {
            "workbookName": source_path.name,
            "workbookSha256": workbook_hash,
            "sheetName": sheet_path.name,
            "sheetSha256": sheet_hash,
        },
        "recordCount": len(records),
        "records": list(records.values()),
        "conversionStats": {
            "rowsWithTitleAndDrawingLink": rows_with_title_and_link,
            "rejectedNonDrawingLinks": rejected_non_drawing_links,
        },
    }
    return result


def main() -> None:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--source", required=True, type=Path)
    argument_parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
    )
    args = argument_parser.parse_args()

    catalog = convert_workbook(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {catalog['recordCount']} unique records to {args.output} "
        f"(schema v{catalog['schemaVersion']})."
    )


if __name__ == "__main__":
    main()