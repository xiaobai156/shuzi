import json
import re
from pathlib import Path
from collections.abc import Iterable
from typing import Any

from kill_numbers.infrastructure.cache_format import (
    build_cache_document,
    failures_from_cache_document,
    records_from_cache_document,
    target_contract_fingerprint,
)
from kill_numbers.infrastructure.file_store import atomic_write_json
from kill_numbers.text_utils import normalize_issue


def _issue_key(value: object) -> str:
    return normalize_issue(str(value or ""))


def _recent_issues_from_latest(latest_issue: str, count: int) -> list[str]:
    last_issue = int(_issue_key(latest_issue))
    first_issue = max(1, last_issue - count + 1)
    return [str(issue) for issue in range(first_issue, last_issue + 1)]


def _dedupe_results(results: Iterable[Any]) -> list[Any]:
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


def _validate_existing_numbers(record: dict, target: dict) -> None:
    issue = _issue_key(record.get("issue", ""))
    raw_numbers = str(record.get("numbers") or "").strip()
    tokens = [token for token in re.split(r"[,\.\s]+", raw_numbers) if token]
    exception = (target.get("cache_issue_exceptions") or {}).get(issue)
    expected_count = (
        exception.get("count")
        if isinstance(exception, dict) and exception.get("approved") is True
        else target.get("count")
    )
    valid_tokens = all(
        re.fullmatch(r"\d{1,2}", token) and 1 <= int(token) <= 49
        for token in tokens
    )
    normalized = [f"{int(token):02d}" for token in tokens] if valid_tokens else []
    if (
        not issue
        or not tokens
        or not valid_tokens
        or not isinstance(expected_count, int)
        or isinstance(expected_count, bool)
        or len(tokens) != expected_count
        or (
            not target.get("allow_duplicate_numbers", False)
            and len(normalized) != len(set(normalized))
        )
    ):
        name = str(record.get("name") or record.get("url") or "未命名")
        raise ValueError(f"缓存文件历史号码无效，已停止覆盖：{name} {issue or '?'}期")


def update_recent_duplicate_cache(
    cache_path: str | Path,
    results: Iterable[Any],
    issues: Iterable[str],
    recent_count: int = 10,
    failures: Iterable[object] | None = None,
    targets: Iterable[dict] | None = None,
) -> None:
    if isinstance(recent_count, bool) or not isinstance(recent_count, int) or recent_count < 1:
        raise ValueError('缓存窗口必须为正整数')
    target_list = [target for target in (targets or []) if isinstance(target, dict)]
    target_by_site = {
        (
            str(target.get("name") or "").strip(),
            str(target.get("url") or "").strip(),
        ): target
        for target in target_list
    }
    requested_issue_list = []
    for issue in issues:
        normalized = normalize_issue(issue)
        if normalized and normalized not in requested_issue_list:
            requested_issue_list.append(normalized)
    requested_issues = set(requested_issue_list)
    if not requested_issues:
        return

    path = Path(cache_path)
    data = {}
    existing_records: list[dict] = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"缓存文件无法读取，已停止覆盖：{path}；{exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"缓存文件格式错误，已停止覆盖：{path}")
        if "sites" in data:
            if not isinstance(data.get("sites"), list):
                raise ValueError(f"缓存文件 sites 格式错误，已停止覆盖：{path}")
            if target_list:
                for site in data["sites"]:
                    if not isinstance(site, dict):
                        raise ValueError(f"缓存文件站点格式错误，已停止覆盖：{path}")
                    key = (
                        str(site.get("name") or "").strip(),
                        str(site.get("url") or "").strip(),
                    )
                    target = target_by_site.get(key)
                    if target is None:
                        continue
                    if site.get("contract") != target_contract_fingerprint(target):
                        raise ValueError(
                            f"缓存文件配置指纹不匹配，已停止覆盖：{key[0] or key[1]}"
                        )
        elif not isinstance(data.get("records"), list):
            raise ValueError(f"缓存文件 records 格式错误，已停止覆盖：{path}")
        existing_records = [
            record for record in records_from_cache_document(data) if isinstance(record, dict)
        ]
        failure_records = failures_from_cache_document(data)
        if not isinstance(failure_records, list):
            raise ValueError(f"缓存文件失败状态格式错误，已停止覆盖：{path}")
        existing_failures = [record for record in failure_records if isinstance(record, dict)]
        if target_list:
            for record in existing_records:
                target = target_by_site.get((
                    str(record.get("name") or "").strip(),
                    str(record.get("url") or "").strip(),
                ))
                if target:
                    _validate_existing_numbers(record, target)
    else:
        existing_failures = []

    new_records = [
        {
            "name": str(item.name),
            "url": str(item.url),
            "issue": normalize_issue(item.issue),
            "numbers": ",".join(item.numbers),
        }
        for item in _dedupe_results(results)
    ]

    def validate_incoming_site(record):
        key = (record['name'], record['url'])
        target = target_by_site.get(key)
        if targets is not None and (target is None or target.get('disabled')):
            raise ValueError(f"缓存更新包含未启用或未指定站点：{record['name']}")
        if record['issue'] not in requested_issues:
            raise ValueError("缓存更新混入非指定期数")
        return target

    for record in new_records:
        target = validate_incoming_site(record)
        _validate_existing_numbers(record, target or {'count': len(record['numbers'].split(','))})

    def record_key(record: dict) -> tuple[str, str, str]:
        return (
            str(record.get("name") or "").strip(),
            str(record.get("url") or "").strip(),
            _issue_key(record.get("issue", "")),
        )

    new_by_key: dict[tuple[str, str, str], dict] = {}
    for record in new_records:
        key = record_key(record)
        previous = new_by_key.get(key)
        if previous and previous["numbers"] != record["numbers"]:
            raise ValueError(f"缓存更新存在同站同期冲突：{record['name']} {record['issue']}期")
        new_by_key[key] = record
    new_record_keys = set(new_by_key)

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
            if issue not in requested_issues:
                raise ValueError("缓存失败状态混入非指定期数")
            record = {
                "name": str(getattr(failure, "name", "") or ""),
                "url": str(getattr(failure, "url", "") or ""),
                "issue": issue,
                "status": "failed",
                "reason": str(getattr(failure, "reason", "") or "未提供失败原因"),
            }
            validate_incoming_site(record)
            key = record_key(record)
            previous = failure_by_key.get(key)
            if previous and previous["reason"] != record["reason"]:
                raise ValueError(f"缓存更新存在同站同期失败冲突：{record['name']} {record['issue']}期")
            failure_by_key[key] = record
            if previous is None:
                new_failures.append(record)

    if new_record_keys & set(failure_by_key):
        raise ValueError("缓存更新同站同期同时存在成功和失败")
    if not new_records and not new_failures:
        return
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
            or normalized["status"] != "failed"
            or not normalized["reason"]
        ):
            continue
        key = record_key(normalized)
        if key in new_record_keys:
            continue
        replacement = failure_by_key.pop(key, None)
        combined_failures.append(replacement or normalized)

    for record in new_failures:
        key = record_key(record)
        if key in new_record_keys:
            continue
        if key in failure_by_key:
            combined_failures.append(failure_by_key.pop(key))

    # Only updated sites roll their own window; unrelated sites remain intact.
    touched = {key[:2] for key in new_record_keys | set(record_key(r) for r in new_failures)}
    latest_by_site = {}
    for record in combined_records + combined_failures:
        site = record_key(record)[:2]
        if site in touched:
            latest_by_site[site] = max(latest_by_site.get(site, 0), int(record["issue"]))
    def keep(record):
        site = record_key(record)[:2]
        return site not in touched or int(record["issue"]) > latest_by_site[site] - recent_count
    output = build_cache_document(
        [r for r in combined_records if keep(r)],
        [r for r in combined_failures if keep(r)],
        recent_count=recent_count, targets=target_list,
    )
    # Preserve IDs, contracts and display settings not supplied by a subset run.
    old_sites = {(s['name'], s['url']): s for s in data.get('sites', [])}
    new_sites = {(s['name'], s['url']): s for s in output['sites']}
    ordered_sites = []
    for key, previous in old_sites.items():
        current = new_sites.pop(key, None)
        if key not in touched or current is None:
            ordered_sites.append(previous)
        else:
            current['id'] = previous['id']
            if key not in target_by_site:
                current = {**previous, 'fingerprint': current['fingerprint']}
            ordered_sites.append(current)
    used_ids = {site['id'] for site in ordered_sites}
    for site in new_sites.values():
        base_id = site['id']
        suffix = 1
        while site['id'] in used_ids:
            site['id'] = f'{base_id}_{suffix}'
            suffix += 1
        used_ids.add(site['id'])
        ordered_sites.append(site)
    output['sites'] = ordered_sites
    atomic_write_json(path, output, trailing_newline=False)
