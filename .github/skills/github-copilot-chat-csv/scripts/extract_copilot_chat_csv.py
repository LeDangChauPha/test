#!/usr/bin/env python3
"""Extract or normalize GitHub Copilot Chat summary records."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

REQUIRED = ("Model", "Prompt", "Credit", "Date")
OUTPUT_FIELDS = (*REQUIRED, "WorkspacePath")
PROMPT_LIMIT = 500


def normalize_prompt(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:PROMPT_LIMIT] or None


def sqlite_paths(root: Path) -> list[Path]:
    return sorted(
        path
        for pattern in ("*.db", "*.sqlite", "*.sqlite3")
        for path in root.rglob(pattern)
        if path.is_file()
    )


def sqlite_records(path: Path) -> tuple[list[dict[str, str | None]], list[str]]:
    """Read a compatible summary table without assuming a fixed DB schema."""
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        ]
        for table in tables:
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
            lookup = {normalized_header(column): column for column in columns}
            if not all(normalized_header(column) in lookup for column in REQUIRED):
                continue
            selected = [lookup[normalized_header(column)] for column in REQUIRED]
            quoted = ", ".join(f'"{column}"' for column in selected)
            rows = connection.execute(f'SELECT {quoted} FROM "{table}"')
            return [
                {field.casefold(): value for field, value in zip(REQUIRED, row)}
                for row in rows
            ], columns
    return [], []


def apply_session_update(requests: list[dict], update: dict) -> None:
    path = update.get("k")
    if not isinstance(path, list) or len(path) < 3 or path[0] != "requests":
        return
    request_index = path[1]
    if not isinstance(request_index, int) or request_index >= len(requests):
        return
    target = requests[request_index]
    for key in path[2:-1]:
        if not isinstance(target, dict):
            return
        target = target.setdefault(key, {})
    if isinstance(target, dict):
        target[path[-1]] = update.get("v")


def session_records(path: Path) -> list[dict[str, str | None]]:
    requests: list[dict] = []
    with path.open("r", encoding="utf-8-sig") as source:
        for line in source:
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("kind") == 0:
                value = event.get("v", {})
                requests = value.get("requests", []) if isinstance(value, dict) else []
            elif event.get("kind") == 2 and event.get("k") == ["requests"]:
                value = event.get("v", [])
                if isinstance(value, list):
                    requests.extend(item for item in value if isinstance(item, dict))
            elif event.get("kind") == 1:
                apply_session_update(requests, event)

    records = []
    for request in requests:
        if not isinstance(request, dict):
            continue
        message = request.get("message", {})
        if not isinstance(message, dict):
            message = {}
        credit = request.get("copilotCredits", request.get("credit"))
        timestamp = request.get("timestamp") or request.get("responseTimestamp")
        if isinstance(timestamp, (int, float)):
            date = datetime.fromtimestamp(timestamp / 1000).astimezone().strftime(
                "%m/%d/%Y %H:%M"
            )
        else:
            date = str(timestamp) if timestamp else None
        records.append(
            {
                "model": request.get("modelId"),
                "prompt": normalize_prompt(message.get("text")),
                "credit": str(credit) if credit is not None else None,
                "date": date,
            }
        )
    return records


def workspace_storage_records(root: Path) -> list[dict[str, str | None]]:
    records: list[dict[str, str | None]] = []
    for workspace in sorted(path for path in root.iterdir() if path.is_dir()):
        copilot_folder = workspace / "GitHub.copilot-chat"
        chat_sessions = workspace / "chatSessions"
        if not (workspace / "state.vscdb").is_file() or not (
            copilot_folder.is_dir() or chat_sessions.is_dir()
        ):
            continue
        session_files = sorted(chat_sessions.glob("*.jsonl"))
        for session in session_files:
            for record in session_records(session):
                record["workspace_path"] = str(workspace)
                records.append(record)
    return records


def read_source(path: Path) -> tuple[list[dict[str, str | None]], list[str]]:
    if path.is_dir():
        if any(
            (child / "state.vscdb").is_file()
            and (
                (child / "GitHub.copilot-chat").is_dir()
                or (child / "chatSessions").is_dir()
            )
            for child in path.iterdir()
            if child.is_dir()
        ):
            records = workspace_storage_records(path)
            if records:
                return records, ["Model", "Prompt", "Credit", "Date"]
            raise ValueError(f"No Copilot chat sessions found under workspace storage {path}")
        for database in sqlite_paths(path):
            records, headers = sqlite_records(database)
            if records:
                return records, headers
        root = path.parent if path.name == "GitHub.copilot-chat" else path
        session_files = sorted((root / "chatSessions").glob("*.jsonl"))
        records = [record for session in session_files for record in session_records(session)]
        if records:
            return records, ["Model", "Prompt", "Credit", "Date"]
        raise ValueError(f"No SQLite table with Model, Prompt, Credit, and Date found under {path}")

    if path.suffix.casefold() in {".db", ".sqlite", ".sqlite3"}:
        records, headers = sqlite_records(path)
        if not records:
            raise ValueError(f"No SQLite table with Model, Prompt, Credit, and Date found in {path}")
        return records, headers

    with path.open("r", encoding="utf-8-sig", newline="") as source:
        sample = source.read(4096)
        source.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(source, dialect=dialect)
        headers = reader.fieldnames or []
        return [
            {key.casefold(): (row.get(key) or "").strip() or None for key in headers}
            for row in reader
        ], headers


def normalized_header(value: str | None) -> str:
    return (value or "").lstrip("\ufeff").strip().casefold()


def parse_date(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def chronological_key(record: dict[str, str | None]) -> tuple[int, str]:
    value = record.get("date") or ""
    for format_string in ("%m/%d/%Y %H:%M", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return (0, datetime.strptime(value, format_string).isoformat())
        except ValueError:
            continue
    return (1, value)


def is_current_month(record: dict[str, str | None]) -> bool:
    value = record.get("date") or ""
    try:
        date = datetime.strptime(value, "%m/%d/%Y %H:%M")
    except ValueError:
        return False
    now = datetime.now()
    return date.year == now.year and date.month == now.month


def timestamped_summary_path(output_directory: Path) -> Path:
    timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S-%f")[:-3]
    return output_directory / timestamp / "chat_summary.csv"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input",
        type=Path,
        help="CSV file, SQLite database, or GitHub.copilot-chat workspace folder",
    )
    parser.add_argument(
        "output_directory",
        type=Path,
        nargs="?",
        default=None,
        help="Output directory (default: chatlog/csv)",
    )
    args = parser.parse_args()
    output_path = timestamped_summary_path(args.output_directory or Path("chatlog") / "csv")

    raw_records, raw_headers = read_source(args.input)
    raw_records = [record for record in raw_records if is_current_month(record)]
    raw_records.sort(key=chronological_key)
    header_map = {normalized_header(name): name for name in raw_headers}
    missing = [name for name in REQUIRED if normalized_header(name) not in header_map]
    records, malformed = [], []
    for row_number, row in enumerate(raw_records, start=2):
        record = {
            "source_row": row_number,
            "model": row.get("model"),
            "prompt": normalize_prompt(row.get("prompt")),
            "credit": row.get("credit"),
            "date": row.get("date"),
            "workspace_path": row.get("workspace_path"),
        }
        record["parsed_date"] = parse_date(record["date"])
        records.append(record)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "Model": record["model"] or "",
                    "Prompt": record["prompt"] or "",
                    "Credit": record["credit"] or "",
                    "Date": record["date"] or "",
                        "WorkspacePath": record["workspace_path"] or "",
                }
            )
    print(f"Wrote {len(records)} record(s) to {output_path}")


if __name__ == "__main__":
    main()
