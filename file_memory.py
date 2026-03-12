"""Read/write reports to ./reports/{run_id}/"""
import json
from pathlib import Path


def write_report(report_dir: str, filename: str, content: str) -> str:
    """Write text content to report_dir/filename. Returns full path."""
    path = Path(report_dir) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def write_json(report_dir: str, filename: str, data: dict) -> str:
    """Write dict as JSON to report_dir/filename. Returns full path."""
    return write_report(report_dir, filename, json.dumps(data, indent=2))


def read_report(path: str) -> str:
    """Read text content from path."""
    return Path(path).read_text(encoding="utf-8")


def read_json(path: str) -> dict:
    """Read JSON file and return as dict."""
    return json.loads(Path(path).read_text(encoding="utf-8"))
