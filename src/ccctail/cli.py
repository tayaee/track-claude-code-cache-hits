import csv
import json
import os
import sys
import time
import unicodedata
from datetime import datetime, timezone
from importlib.metadata import version as pkg_version
from pathlib import Path

import click
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

TARGET_DIR = Path.home() / ".claude" / "projects"

ALL_COLUMNS = [
    "timestamp",
    "hits",
    "misses",
    "ratio",
    "cum-hits",
    "cum-misses",
    "cum-ratio",
    "project",
    "content",
]

DEFAULT_COLUMNS = ["timestamp", "hits", "misses", "ratio", "project", "content"]

COLUMN_SPECS: dict[str, tuple[str, int]] = {
    "timestamp": ("<", 24),
    "hits": (">", 8),
    "misses": (">", 6),
    "ratio": (">", 5),
    "cum-hits": (">", 12),
    "cum-misses": (">", 12),
    "cum-ratio": (">", 9),
    "project": ("<", 25),
    "content": ("<", 80),
    "model": ("<", 30),
}


def _format_cell(col: str, val) -> str:
    if col in ("hits", "misses", "cum-hits", "cum-misses"):
        return f"{val:,}"
    if col in ("ratio", "cum-ratio"):
        return f"{val:.0f}%"
    if col == "project":
        max_w = COLUMN_SPECS["project"][1]
        s = str(val)
        if len(s) > max_w:
            half = (max_w - 2) // 2
            return s[:half] + ".." + s[-half:]
        return s
    return str(val)


_COLUMN_INDEX: dict[str, int] = {c: i for i, c in enumerate(ALL_COLUMNS)}


def _validate_column(name: str) -> list[str]:
    """Return empty list if valid, else list with the invalid name."""
    return [] if name in ALL_COLUMNS else [name]


def _resolve_columns(
    columns_str: str | None, column_order_str: str | None
) -> tuple[list[str] | None, list[str]]:
    """Return (resolved_columns, invalid_names). resolved_columns is None on error."""
    if column_order_str is not None:
        cols = [c.strip() for c in column_order_str.split(",") if c.strip()]
        invalid = [c for c in cols if c not in ALL_COLUMNS]
        if invalid:
            return None, invalid
        return cols, []

    if columns_str is not None:
        parts = [c.strip() for c in columns_str.split(",") if c.strip()]
        if any(p.startswith("+") or p.startswith("-") for p in parts):
            result = list(DEFAULT_COLUMNS)
            for part in parts:
                if part.startswith("+"):
                    name = part[1:]
                    if invalid := _validate_column(name):
                        return None, invalid
                    if name not in result:
                        std_idx = _COLUMN_INDEX[name]
                        insert_pos = len(result)
                        for i, col in enumerate(result):
                            if _COLUMN_INDEX[col] > std_idx:
                                insert_pos = i
                                break
                        result.insert(insert_pos, name)
                elif part.startswith("-"):
                    name = part[1:]
                    if invalid := _validate_column(name):
                        return None, invalid
                    if name in result:
                        result.remove(name)
            return result, []
        else:
            invalid = [c for c in parts if c not in ALL_COLUMNS]
            if invalid:
                return None, invalid
            return parts, []

    return list(DEFAULT_COLUMNS), []


def _truncate_to_width(text: str, max_width: int) -> str:
    width = 0
    result = []
    for ch in text:
        ea = unicodedata.east_asian_width(ch)
        char_width = 2 if ea in ("F", "W") else 1
        if width + char_width > max_width:
            break
        result.append(ch)
        width += char_width
    return "".join(result)


def get_log_files():
    return sorted(
        [p for p in TARGET_DIR.glob("**/*.jsonl") if p.is_file()],
        key=lambda p: (p.stat().st_mtime, str(p)),
    )


def load_all_log_entries():
    entries = []
    jsonl_files = get_log_files()
    for jsonl_file in jsonl_files:
        path_obj = Path(jsonl_file)
        project_name = path_obj.parent.name
        if project_name == "projects":
            project_name = path_obj.stem
        try:
            with open(jsonl_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        entries.append((project_name, line))
        except (OSError, UnicodeDecodeError):
            continue
    return entries, jsonl_files


def dump_existing_logs(tracker, lines_option=None):
    entries, jsonl_files = load_all_log_entries()

    # Determine display limit
    if lines_option is None:
        display_count = 10
    elif isinstance(lines_option, str) and lines_option.startswith("+"):
        display_count = None  # show all
    else:
        try:
            display_count = int(lines_option)
        except (TypeError, ValueError):
            display_count = 10

    # Enable buffering when display_count is set (not None and not showing all)
    if display_count is not None:
        tracker.buffering = True

    # Sort by timestamp when dumping all (--all)
    if display_count is None:

        def _entry_timestamp(entry):
            try:
                d = json.loads(entry[1].strip())
                return d.get("timestamp", "")
            except (json.JSONDecodeError, IndexError):
                return ""

        entries.sort(key=_entry_timestamp)

    for project_name, line in entries:
        tracker.process_line(project_name, line)

    # Flush buffered output
    if tracker.buffering and display_count is not None:
        tracker.flush_buffer(display_count)

    tracker.file_positions = {
        str(jsonl_file): os.path.getsize(jsonl_file) for jsonl_file in jsonl_files
    }


class CacheTracker:
    def __init__(
        self,
        fmt: str = "plain",
        since: str | None = None,
        columns: list[str] | None = None,
    ):
        self.project_stats: dict[str, dict[str, int]] = {}
        self.cum_hits: int = 0
        self.cum_misses: int = 0
        self.file_positions: dict[str, int] = {}
        self.last_user_content: dict[str, str] = {}
        self.line_count: int = 1
        self.output_buffer: list[dict] = []
        self.buffering: bool = False
        self.fmt: str = fmt
        self._csv_writer: csv.writer | None = None
        self._csv_header_written: bool = False
        self.since: datetime | None = None
        if since:
            dt = datetime.fromisoformat(since)
            self.since = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        self.columns: list[str] = (
            columns if columns is not None else list(DEFAULT_COLUMNS)
        )

        if fmt == "csv":
            self._csv_writer = csv.writer(sys.stdout)

    @staticmethod
    def _format_row(columns: list[str], get_value) -> str:
        parts = []
        for col in columns:
            align, width = COLUMN_SPECS[col]
            formatted = get_value(col)
            if width > 0:
                parts.append(f"{formatted:{align}{width}}")
            else:
                parts.append(formatted)
        return " | ".join(parts)

    @staticmethod
    def _extract_text(val):
        """Extract a short text summary from various content formats."""
        if isinstance(val, str):
            return (
                val.replace("\r\n", "\\n")
                .replace("\r", "")
                .replace("\n", "\\n")
                .strip()
            )
        if isinstance(val, list):
            parts = []
            for part in val:
                if isinstance(part, dict):
                    if part.get("type") == "tool_result":
                        tc = part.get("content", "")
                        if isinstance(tc, str):
                            parts.append(tc)
                        elif isinstance(tc, list):
                            for sub in tc:
                                if isinstance(sub, dict):
                                    parts.append(sub.get("text", ""))
                                elif isinstance(sub, str):
                                    parts.append(sub)
                    elif part.get("type") == "text":
                        parts.append(part.get("text", ""))
                    elif part.get("type") == "tool_use":
                        name = part.get("name", "tool")
                        inp = part.get("input", {})
                        file_path = inp.get("file_path", "")
                        pattern = inp.get("pattern", "")
                        base_path = inp.get("path", "")
                        command = inp.get("command", "")
                        url = inp.get("url", "")
                        query = inp.get("query", "")
                        if file_path:
                            obj = file_path
                        elif pattern:
                            obj = (
                                str(Path(base_path) / pattern) if base_path else pattern
                            )
                        elif command:
                            obj = command[:60]
                        elif url:
                            obj = url
                        elif query:
                            obj = query
                        else:
                            obj = ""
                        parts.append(f"[{name}] {obj}" if obj else f"[{name}]")
                    elif part.get("type") == "thinking":
                        pass  # fall back to last user prompt
                elif isinstance(part, str):
                    parts.append(part)
            return (
                " ".join(p for p in parts if p)
                .replace("\r\n", "\\n")
                .replace("\r", "")
                .replace("\n", "\\n")
                .strip()
            )
        return ""

    def _emit(self, record: dict) -> None:
        if self.buffering:
            self.output_buffer.append(record)
            return
        self._print_record(record)

    def _print_record(self, record: dict) -> None:
        if self.fmt == "plain":
            if (sys.stdout.isatty() and self.line_count % 10 == 1) or (
                not sys.stdout.isatty() and self.line_count == 1
            ):
                header = self._format_row(self.columns, lambda c: c)
                print("-" * len(header))
                print(header)
                print("-" * len(header))
            print(self._format_row(self.columns, lambda c: _format_cell(c, record[c])))
        elif self.fmt == "csv":
            cols = self.columns + ["model"]
            if not self._csv_header_written:
                self._csv_writer.writerow(cols)
                self._csv_header_written = True
            self._csv_writer.writerow([record.get(col, "") for col in cols])
        elif self.fmt == "json":
            cols = self.columns + ["model"]
            print(
                json.dumps(
                    {col: record.get(col, "") for col in cols}, ensure_ascii=False
                )
            )

    def flush_buffer(self, count: int) -> None:
        records = self.output_buffer[-count:]
        # Reset line_count so plain headers are correct
        self.line_count = 1
        for record in records:
            self._print_record(record)
            self.line_count += 1
        self.output_buffer.clear()
        self.buffering = False

    def process_line(self, project_name, line):
        try:
            d = json.loads(line.strip())
        except json.JSONDecodeError:
            return

        timestamp = d.get("timestamp", "")
        msg_type = d.get("type", "")
        message = d.get("message", {})

        if self.since and timestamp:
            try:
                ts_dt = datetime.fromisoformat(timestamp)
                if ts_dt.tzinfo is None:
                    ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                if ts_dt < self.since:
                    return
            except ValueError:
                pass

        # Extract content for user messages (skip meta/tool-result noise)
        if msg_type == "user" and message.get("role") == "user":
            if not d.get("isMeta") and not d.get("sourceToolAssistantUUID"):
                raw = message.get("content", "")
                content = self._extract_text(raw)
                if content:
                    self.last_user_content[project_name] = _truncate_to_width(
                        content, COLUMN_SPECS["content"][1]
                    )
            return

        # Extract content for assistant messages
        current_content = ""
        if msg_type == "assistant":
            raw = message.get("content", "")
            current_content = self._extract_text(raw)
        elif msg_type == "system":
            subtype = d.get("subtype", "")
            current_content = f"[system:{subtype}]" if subtype else "[system]"
        elif msg_type == "file-history-snapshot":
            current_content = "[file-snapshot]"

        u = d.get("usage") or message.get("usage")
        if not u:
            return

        cr = u.get("cache_read_input_tokens", 0)
        cc = u.get("cache_creation_input_tokens", 0)
        it = u.get("input_tokens", 0)

        total = cr + cc + it
        if total == 0:
            return

        if project_name not in self.project_stats:
            self.project_stats[project_name] = {"hits": 0, "misses": 0}

        self.project_stats[project_name]["hits"] += cr
        self.project_stats[project_name]["misses"] += cc
        self.cum_hits += cr
        self.cum_misses += cc

        ratio = (cr / total * 100) if total > 0 else 0
        cum_total = self.cum_hits + self.cum_misses
        cum_ratio = (self.cum_hits / cum_total * 100) if cum_total > 0 else 0

        model_name = message.get("model", "") if msg_type == "assistant" else ""

        if current_content:
            if msg_type == "assistant":
                label = model_name if model_name else "Assistant"
                content_preview = f"[{label}] {current_content}"
            else:
                content_preview = current_content
        else:
            content_preview = (
                f"[User] {self.last_user_content.get(project_name, 'N/A')}"
            )

        content_preview = _truncate_to_width(
            content_preview, COLUMN_SPECS["content"][1]
        )

        record = {
            "timestamp": timestamp,
            "hits": cr,
            "misses": cc,
            "ratio": round(ratio, 2),
            "cum-hits": self.cum_hits,
            "cum-misses": self.cum_misses,
            "cum-ratio": round(cum_ratio, 2),
            "project": project_name,
            "content": content_preview,
            "model": model_name,
        }

        if not self.buffering:
            self.line_count += 1
        self._emit(record)

    def read_new_lines(self, file_path):
        path_obj = Path(file_path)
        project_name = path_obj.parent.name
        if project_name == "projects":
            project_name = path_obj.stem
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                last_pos = self.file_positions.get(file_path, 0)
                f.seek(last_pos)
                for line in f:
                    if line.strip():
                        self.process_line(project_name, line)
                self.file_positions[file_path] = f.tell()
        except (OSError, UnicodeDecodeError):
            return


class LogHandler(FileSystemEventHandler):
    def __init__(self, tracker):
        self.tracker = tracker

    def _handle_event(self, event):
        if not event.is_directory and event.src_path.endswith(".jsonl"):
            self.tracker.read_new_lines(event.src_path)

    on_modified = on_created = _handle_event


@click.command()
@click.version_option(version=pkg_version("ccctail"), message="ccctail %(version)s")
@click.option(
    "-n",
    "--lines",
    "lines",
    type=str,
    default=None,
    help="Show the last N entries. 10 by default.",
)
@click.option(
    "-a",
    "--all",
    "dump_all",
    is_flag=True,
    help="Dump all existing logs.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["plain", "csv", "json"]),
    default="plain",
    help="Output format. Default: plain.",
)
@click.option(
    "-f",
    "--follow",
    "do_follow",
    is_flag=True,
    default=False,
    help="Follow new log entries (like tail -f).",
)
@click.option(
    "--since",
    "since",
    type=str,
    default=None,
    help="Show only entries at or after this ISO 8601 timestamp (e.g. 2026-04-01T17:59:51.854Z).",
)
@click.option(
    "--columns",
    "columns",
    type=str,
    default=None,
    help=(
        "Toggle columns with +name,-name,... relative to default columns "
        f"({', '.join(DEFAULT_COLUMNS)}). "
        f"Available: {', '.join(ALL_COLUMNS)}"
    ),
)
@click.option(
    "--column-order",
    "column_order",
    type=str,
    default=None,
    help=(
        "Show only the listed columns in the given order (comma-separated). "
        f"Available: {', '.join(ALL_COLUMNS)}"
    ),
)
def main(
    lines: str,
    dump_all: bool,
    fmt: str,
    do_follow: bool,
    since: str | None,
    columns: str | None,
    column_order: str | None,
):
    if not TARGET_DIR.exists():
        click.echo(f"Error: Directory {TARGET_DIR} does not exist.")
        return

    col_list, invalid = _resolve_columns(columns, column_order)
    if invalid:
        click.echo(f"Error: Unknown column(s): {', '.join(invalid)}")
        click.echo(f"Available columns: {', '.join(ALL_COLUMNS)}")
        return

    if dump_all:
        lines_option = "+1"
    elif lines is None:
        lines_option = "10"
    else:
        lines_option = lines.lstrip("=")

    quiet = fmt != "plain"
    follow = do_follow

    tracker = CacheTracker(fmt=fmt, since=since, columns=col_list)

    if not quiet:
        click.echo(f"Monitoring Claude Code cache logs in {TARGET_DIR}...")

    if not quiet:
        if lines_option == "+1" or dump_all:
            click.echo("Dumping all existing logs...")
        else:
            click.echo(f"Dumping existing logs with --lines={lines_option}...")

    dump_existing_logs(tracker, lines_option)

    if not follow:
        return

    handler = LogHandler(tracker)
    observer = Observer()
    observer.schedule(handler, str(TARGET_DIR), recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        click.echo("\nStopping monitor...")
    observer.join()


if __name__ == "__main__":
    main()
