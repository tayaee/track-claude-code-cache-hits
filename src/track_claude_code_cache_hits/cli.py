import json
import os
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

TARGET_DIR = Path.home() / ".claude" / "projects"


HEADER = (
    f"{'Hits':>12} | {'Misses':>12} | {'Ratio':>5} | "
    f"{'Cum-hits':>14} | {'Cum-misses':>14} | {'Cum-ratio':>9} | "
    f"{'Project':<32} | Request"
)


class CacheTracker:
    def __init__(self):
        self.project_stats = {}
        self.cum_hits = 0
        self.cum_misses = 0
        self.file_positions = {}
        self.last_user_content = {}
        self.line_count = 1

    def process_line(self, project_name, line):
        try:
            d = json.loads(line.strip())
        except json.JSONDecodeError:
            return

        message = d.get("message", {})
        if d.get("type") == "user" and message.get("role") == "user":
            content = message.get("content", "").replace("\n", " ")
            self.last_user_content[project_name] = content[:50]
            return

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

        content_preview = self.last_user_content.get(project_name, "N/A")

        if self.line_count % 10 == 0:
            print(HEADER)
            print("-" * 132)
        self.line_count += 1

        print(
            f"{cr:>12,} | {cc:>12,} | {ratio:>4.0f}% | "
            f"{self.cum_hits:>14,} | {self.cum_misses:>14,} | {cum_ratio:>8.0f}% | "
            f"{project_name[:32]:<32} | "
            f"{content_preview}"
        )

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


def main():
    if not TARGET_DIR.exists():
        print(f"Error: Directory {TARGET_DIR} does not exist.")
        return

    print(f"Monitoring Claude Code cache logs in {TARGET_DIR}...")
    print(HEADER)
    print("-" * 132)
    tracker = CacheTracker()
    handler = LogHandler(tracker)
    observer = Observer()
    for jsonl_file in TARGET_DIR.glob("**/*.jsonl"):
        tracker.file_positions[str(jsonl_file)] = os.path.getsize(jsonl_file)
    observer.schedule(handler, str(TARGET_DIR), recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("\nStopping monitor...")
    observer.join()


if __name__ == "__main__":
    main()
