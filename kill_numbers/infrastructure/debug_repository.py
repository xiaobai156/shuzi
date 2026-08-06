import re
import time
from pathlib import Path
from urllib.parse import urlparse
from typing import Mapping


def safe_filename(value: str, limit: int = 80) -> str:
    value = re.sub(r"[\\/:*?\"<>|\s]+", "_", value or "")
    value = value.strip("._")
    return (value[:limit] or "unknown")


def debug_file_for(
    debug_dir: str | Path,
    target: Mapping[str, object],
    issues: list[str],
    name: str,
    normalize_issue,
) -> Path:
    issue_text = "-".join(normalize_issue(issue) for issue in issues)
    url = str(target.get("url") or "")
    url_part = safe_filename(urlparse(url).netloc)
    name_part = safe_filename(name or str(target.get("name") or "unknown"))
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return Path(debug_dir) / f"{issue_text}_{name_part}_{url_part}_{timestamp}.txt"


def save_debug_page(
    debug_dir: str | Path,
    target: Mapping[str, object],
    issues: list[str],
    name: str,
    content: str,
    reason: str,
    normalize_issue,
    atomic_write_text,
) -> Path | None:
    if not content:
        return None
    path = debug_file_for(debug_dir, target, issues, name, normalize_issue)
    lines = [
        f"name: {name}",
        f"url: {target['url']}",
        f"issues: {','.join(issues)}",
        f"reason: {reason}",
        "",
        content,
    ]
    atomic_write_text(path, "\n".join(lines))
    return path
