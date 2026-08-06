import json
import time
from pathlib import Path
from collections.abc import Iterable

from kill_numbers.infrastructure.file_store import atomic_write_json
from kill_numbers.text_utils import normalize_issue


def _issue_key(value: object) -> str:
    return normalize_issue(value)


def _recent_issues_from_latest(latest_issue: str, count: int) -> list[str]:
    last_issue = int(_issue_key(latest_issue))
    first_issue = max(1, last_issue - count + 1)
    return [str(issue) for issue in range(first_issue, last_issue + 1)]


def _dedupe_results(results: Iterable[object]) -> list[object]:
    seen = set()
    deduped = []
    for item in results:
        key = (
            str(getattr(item, "url", "")),
            str(getattr(item, "name", "")),
            str(getattr(item, "issue", "")),
            tuple(getattr(item, "numbers", ()) or ()),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def update_recent_duplicate_cache(
    cache_path: str | Path,
    results: Iterable[object],
    issues: Iterable[str],
    recent_count: int = 10,
    failures: Iterable[object] | None = None,
) -> None:
    requested_issue_list = []
    for issue in issues:
        normalized = normalize_issue(issue)
        if normalized and normalized not in requested_issue_list:
            requested_issue_list.append(normalized)
    requested_issues = set(requested_issue_list)
    if not requested_issues:
        return

    path = Path(cache_path)
    existing_records: list[dict] = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"缓存文件无法读取，已停止覆盖：{path}；{exc}") from exc
        records = data.get("records") if isinstance(data, dict) else None
        if not isinstance(records, list):
            raise ValueError(f"缓存文件格式错误，已停止覆盖：{path}")
        existing_records = [record for record in records if isinstance(record, dict)]
        failure_records = data.get("failures", [])
        if not isinstance(failure_records, list):
            raise ValueError(f"缓存文件失败状态格式错误，已停止覆盖：{path}")
        existing_failures = [record for record in failure_records if isinstance(record, dict)]
    else:
        existing_failures = []

    existing_issues = [
        int(_issue_key(record.get("issue", "")))
        for record in existing_records
        if _issue_key(record.get("issue", ""))
    ]
    existing_issues.extend(
        int(_issue_key(record.get("issue", "")))
        for record in existing_failures
        if _issue_key(record.get("issue", ""))
    )
    latest_issue = max([int(issue) for issue in requested_issues] + existing_issues)
    wanted_issues = set(_recent_issues_from_latest(str(latest_issue), recent_count))

    new_records = [
        {
            "name": str(item.name),
            "url": str(item.url),
            "issue": normalize_issue(item.issue),
            "numbers": ",".join(item.numbers),
        }
        for item in _dedupe_results(results)
        if normalize_issue(item.issue) in wanted_issues
    ]

    def record_key(record: dict) -> tuple[str, str, str]:
        return (
            str(record.get("name") or "").strip(),
            str(record.get("url") or "").strip().lower(),
            _issue_key(record.get("issue", "")),
        )

    new_by_key: dict[tuple[str, str, str], dict] = {}
    for record in new_records:
        key = record_key(record)
        previous = new_by_key.get(key)
        if previous and previous["numbers"] != record["numbers"]:
            raise ValueError(f"缓存更新存在同站同期冲突：{record['name']} {record['issue']}期")
        new_by_key[key] = record

    new_failures: list[dict] = []
    failure_by_key: dict[tuple[str, str, str], dict] = {}
    for failure in failures or []:
        failure_issues = []
        raw_failure_issue = str(getattr(failure, "issue", "") or "").strip()
        failure_issue = _issue_key(raw_failure_issue) if raw_failure_issue else ""
        if failure_issue:
            failure_issues.append(failure_issue)
        else:
            failure_issues.extend(requested_issue_list)
        for issue in failure_issues:
            if issue not in wanted_issues:
                continue
            record = {
                "name": str(getattr(failure, "name", "") or ""),
                "url": str(getattr(failure, "url", "") or ""),
                "issue": issue,
                "status": "failed",
                "reason": str(getattr(failure, "reason", "") or "未提供失败原因"),
            }
            key = record_key(record)
            previous = failure_by_key.get(key)
            if previous and previous["reason"] != record["reason"]:
                raise ValueError(f"缓存更新存在同站同期失败冲突：{record['name']} {record['issue']}期")
            failure_by_key[key] = record
            if previous is None:
                new_failures.append(record)

    combined_records = []
    for record in existing_records:
        normalized = {
            "name": str(record.get("name") or ""),
            "url": str(record.get("url") or ""),
            "issue": _issue_key(record.get("issue", "")),
            "numbers": str(record.get("numbers") or ""),
        }
        if (
            not normalized["name"]
            or not normalized["issue"]
            or not normalized["numbers"]
            or normalized["issue"] not in wanted_issues
        ):
            continue
        key = record_key(normalized)
        if key in failure_by_key:
            continue
        replacement = new_by_key.pop(key, None)
        combined_records.append(replacement or normalized)

    for record in new_records:
        key = record_key(record)
        if key in new_by_key:
            combined_records.append(new_by_key.pop(key))

    combined_failures = []
    for record in existing_failures:
        normalized = {
            "name": str(record.get("name") or ""),
            "url": str(record.get("url") or ""),
            "issue": _issue_key(record.get("issue", "")),
            "status": str(record.get("status") or ""),
            "reason": str(record.get("reason") or ""),
        }
        if (
            not normalized["name"]
            or not normalized["issue"]
            or normalized["issue"] not in wanted_issues
            or normalized["status"] != "failed"
            or not normalized["reason"]
        ):
            continue
        key = record_key(normalized)
        if key in new_by_key:
            continue
        replacement = failure_by_key.pop(key, None)
        combined_failures.append(replacement or normalized)

    for record in new_failures:
        key = record_key(record)
        if key in failure_by_key:
            combined_failures.append(failure_by_key.pop(key))

    atomic_write_json(
        path,
        {
            "version": 1,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "recent_count": recent_count,
            "records": combined_records,
            "failures": combined_failures,
        },
        trailing_newline=False,
    )
