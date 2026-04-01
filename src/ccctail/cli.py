import csv
import json
import os
import sys
import time
from pathlib import Path

import click
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

TARGET_DIR = Path.home() / ".claude" / "projects"


HEADER = (
    f"{'Hits':>12} | {'Misses':>12} | {'Ratio':>5} | "
    f"{'Cum-hits':>14} | {'Cum-misses':>14} | {'Cum-ratio':>9} | "
    f"{'Project':<32} | Content"
)

CSV_COLUMNS = [
    "hits",
    "misses",
    "ratio",
    "cum_hits",
    "cum_misses",
    "cum_ratio",
    "project",
    "content",
]


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

    for project_name, line in entries:
        tracker.process_line(project_name, line)

    # Flush buffered output
    if tracker.buffering and display_count is not None:
        tracker.flush_buffer(display_count)

    tracker.file_positions = {
        str(jsonl_file): os.path.getsize(jsonl_file) for jsonl_file in jsonl_files
    }


class CacheTracker:
    def __init__(self, fmt: str = "plain"):
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

        if fmt == "csv":
            self._csv_writer = csv.writer(sys.stdout)

    @staticmethod
    def _extract_text(val):
        """Extract a short text summary from various content formats."""
        if isinstance(val, str):
            return val.replace("\n", " ").strip()
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
            return " ".join(p for p in parts if p).replace("\n", " ").strip()
        return ""

    def _emit(self, record: dict) -> None:
        if self.buffering:
            self.output_buffer.append(record)
            return
        self._print_record(record)

    def _print_record(self, record: dict) -> None:
        if self.fmt == "plain":
            if sys.stdout.isatty() and self.line_count % 10 == 1:
                print("-" * 132)
                print(HEADER)
                print("-" * 132)
            proj = record["project"]
            if len(proj) > 32:
                proj = proj[:15] + ".." + proj[-15:]
            print(
                f"{record['hits']:>12,} | {record['misses']:>12,} | "
                f"{record['ratio']:>4.0f}% | "
                f"{record['cum_hits']:>14,} | {record['cum_misses']:>14,} | "
                f"{record['cum_ratio']:>8.0f}% | "
                f"{proj:<32} | "
                f"{record['content']}"
            )
        elif self.fmt == "csv":
            if not self._csv_header_written:
                self._csv_writer.writerow(CSV_COLUMNS)
                self._csv_header_written = True
            self._csv_writer.writerow(
                [
                    record["hits"],
                    record["misses"],
                    round(record["ratio"], 2),
                    record["cum_hits"],
                    record["cum_misses"],
                    round(record["cum_ratio"], 2),
                    record["project"],
                    record["content"],
                ]
            )
        elif self.fmt == "json":
            print(json.dumps(record, ensure_ascii=False))

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

        msg_type = d.get("type", "")
        message = d.get("message", {})

        # Extract content for user messages (skip meta/tool-result noise)
        if msg_type == "user" and message.get("role") == "user":
            if not d.get("isMeta") and not d.get("sourceToolAssistantUUID"):
                raw = message.get("content", "")
                content = self._extract_text(raw)
                if content:
                    self.last_user_content[project_name] = content[:50]
            return

        # Extract content for assistant messages
        current_content = ""
        if msg_type == "assistant":
            raw = message.get("content", "")
            current_content = self._extract_text(raw)[:50]
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

        if current_content:
            if msg_type == "assistant":
                content_preview = f"[Assistant] {current_content}"
            else:
                content_preview = current_content
        else:
            content_preview = (
                f"[User] {self.last_user_content.get(project_name, 'N/A')}"
            )

        record = {
            "hits": cr,
            "misses": cc,
            "ratio": round(ratio, 2),
            "cum_hits": self.cum_hits,
            "cum_misses": self.cum_misses,
            "cum_ratio": round(cum_ratio, 2),
            "project": project_name,
            "content": content_preview,
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

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith(".jsonl"):
            self.tracker.read_new_lines(event.src_path)

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith(".jsonl"):
            self.tracker.read_new_lines(event.src_path)


@click.command()
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
def main(lines: str, dump_all: bool, fmt: str, do_follow: bool):
    if not TARGET_DIR.exists():
        click.echo(f"Error: Directory {TARGET_DIR} does not exist.")
        return

    if dump_all:
        lines_option = "+1"
    elif lines is None:
        lines_option = "10"
    else:
        lines_option = lines.lstrip("=")

    quiet = fmt != "plain"
    follow = do_follow and fmt == "plain"

    tracker = CacheTracker(fmt=fmt)

    if not quiet:
        click.echo(f"Monitoring Claude Code cache logs in {TARGET_DIR}...")
        # click.echo(HEADER)
        # click.echo("-" * 132)

    if not quiet:
        if lines_option == "+1" or dump_all:
            click.echo("Dumping all existing logs...")
        else:
            click.echo(f"Dumping existing logs with lines={lines_option}...")

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
