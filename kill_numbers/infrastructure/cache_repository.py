import json
import time
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from kill_numbers.infrastructure.file_store import atomic_write_json
from kill_numbers.text_utils import normalize_issue


CACHE_VERSION = 2
CYCLE_WRAP_THRESHOLD = 100


def _issue_key(value: object) -> str:
    return normalize_issue(value)


def _site_key(name: str, url: str) -> tuple[str, str]:
    normalized_url = str(url or "").strip().lower()
    if normalized_url:
        return "url", normalized_url
    return "name", str(name or "").strip()


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


def _infer_next_cycle(last_issue: str | None, last_cycle: int, new_issue: str) -> int:
    if last_issue is None:
        return 0
    previous = int(last_issue)
    current = int(new_issue)
    if current == previous:
        return last_cycle
    if current > previous:
        if current - previous >= CYCLE_WRAP_THRESHOLD and last_cycle > 0:
            raise ValueError(
                f"期号从 {previous} 大幅回退到旧周期 {current}，缓存保持不变"
            )
        return last_cycle
    if previous - current >= CYCLE_WRAP_THRESHOLD:
        return last_cycle + 1
    raise ValueError(f"期号从 {previous} 回退到 {current}，缓存保持不变")


def _migrate_v1(data: dict, path: Path) -> tuple[list[dict], list[dict], list[dict], int]:
    records = data.get("records")
    failures = data.get("failures", [])
    if not isinstance(records, list) or not isinstance(failures, list):
        raise ValueError(f"缓存文件格式错误，已停止覆盖：{path}")
    if failures:
        raise ValueError(
            f"缓存版本1含失败状态，无法可靠推断时间顺序，请先重建缓存：{path}"
        )

    timeline: list[dict] = []
    per_site_last: dict[tuple[str, str], tuple[str, int]] = {}
    sequence = 0
    for record in records:
        if not isinstance(record, dict):
            raise ValueError(f"缓存版本1含无效记录，请先重建缓存：{path}")
        name = str(record.get("name") or "")
        url = str(record.get("url") or "")
        issue = _issue_key(record.get("issue", ""))
        site = _site_key(name, url)
        last_issue, last_cycle = per_site_last.get(site, (None, 0))
        try:
            cycle = _infer_next_cycle(last_issue, last_cycle, issue)
        except ValueError:
            # A v1 file may have been sorted rather than appended.  Refuse to
            # guess instead of silently corrupting cycle identity.
            raise ValueError(
                f"缓存版本1无法可靠推断 {name or url} 的周期顺序，请重建缓存：{path}"
            )
        sequence += 1
        timeline.append(
            {
                "name": name,
                "url": url,
                "issue": issue,
                "status": "success",
                "sequence": sequence,
                "cycle": cycle,
            }
        )
        per_site_last[site] = (issue, cycle)
    return [dict(item) for item in records], [], timeline, sequence


def _load_cache(path: Path) -> tuple[list[dict], list[dict], list[dict], int]:
    if not path.exists():
        return [], [], [], 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"缓存文件无法读取，已停止覆盖：{path}；{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"缓存文件格式错误，已停止覆盖：{path}")
    version = data.get("version", 1)
    if version == 1:
        return _migrate_v1(data, path)
    if version != CACHE_VERSION:
        raise ValueError(f"缓存版本不支持，已停止覆盖：{path}；version={version}")

    records = data.get("records")
    failures = data.get("failures", [])
    timeline = data.get("timeline")
    run_sequence = data.get("run_sequence", 0)
    if not isinstance(records, list) or not isinstance(failures, list) or not isinstance(timeline, list):
        raise ValueError(f"缓存文件格式错误，已停止覆盖：{path}")
    if isinstance(run_sequence, bool) or not isinstance(run_sequence, int) or run_sequence < 0:
        raise ValueError(f"缓存 run_sequence 无效，已停止覆盖：{path}")
    return (
        [dict(item) for item in records if isinstance(item, dict)],
        [dict(item) for item in failures if isinstance(item, dict)],
        [dict(item) for item in timeline if isinstance(item, dict)],
        run_sequence,
    )


def update_recent_duplicate_cache(
    cache_path: str | Path,
    results: Iterable[object],
    issues: Iterable[str],
    recent_count: int = 10,
    failures: Iterable[object] | None = None,
) -> None:
    if isinstance(recent_count, bool) or not isinstance(recent_count, int) or recent_count <= 0:
        raise ValueError(f"recent_count 必须是正整数：{recent_count!r}")

    requested_issue_list: list[str] = []
    for issue in issues:
        normalized = normalize_issue(issue)
        if normalized and normalized not in requested_issue_list:
            requested_issue_list.append(normalized)
    if not requested_issue_list:
        return

    path = Path(cache_path)
    existing_records, existing_failures, timeline, run_sequence = _load_cache(path)

    record_payload: dict[tuple[tuple[str, str], str], dict] = {}
    for record in existing_records:
        name = str(record.get("name") or "")
        url = str(record.get("url") or "")
        issue = _issue_key(record.get("issue", ""))
        numbers = str(record.get("numbers") or "")
        if not name or not issue or not numbers:
            raise ValueError(f"缓存成功记录无效，已停止覆盖：{path}")
        key = (_site_key(name, url), issue)
        previous = record_payload.get(key)
        if previous and previous["numbers"] != numbers:
            raise ValueError(f"缓存同站同期成功冲突：{name} {issue}期")
        record_payload[key] = {"name": name, "url": url, "issue": issue, "numbers": numbers}

    failure_payload: dict[tuple[tuple[str, str], str], dict] = {}
    for record in existing_failures:
        name = str(record.get("name") or "")
        url = str(record.get("url") or "")
        issue = _issue_key(record.get("issue", ""))
        reason = str(record.get("reason") or "")
        if not name or not issue or record.get("status") != "failed" or not reason or "numbers" in record:
            raise ValueError(f"缓存失败状态无效，已停止覆盖：{path}")
        key = (_site_key(name, url), issue)
        failure_payload[key] = {
            "name": name,
            "url": url,
            "issue": issue,
            "status": "failed",
            "reason": reason,
        }

    overlap = set(record_payload) & set(failure_payload)
    if overlap:
        raise ValueError(f"缓存同站同期同时存在成功和失败状态：{sorted(overlap)!r}")

    timeline_by_period: dict[tuple[tuple[str, str], int, str], dict] = {}
    for entry in timeline:
        name = str(entry.get("name") or "")
        url = str(entry.get("url") or "")
        issue = _issue_key(entry.get("issue", ""))
        status = str(entry.get("status") or "")
        sequence = entry.get("sequence")
        cycle = entry.get("cycle")
        if (
            not name
            or status not in {"success", "failed"}
            or isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence <= 0
            or isinstance(cycle, bool)
            or not isinstance(cycle, int)
            or cycle < 0
        ):
            raise ValueError(f"缓存时间线无效，已停止覆盖：{path}")
        period_key = (_site_key(name, url), cycle, issue)
        if period_key in timeline_by_period:
            raise ValueError(f"缓存时间线同站同期冲突：{name} cycle={cycle} issue={issue}")
        timeline_by_period[period_key] = {
            "name": name,
            "url": url,
            "issue": issue,
            "status": status,
            "sequence": sequence,
            "cycle": cycle,
        }

    incoming_success: dict[tuple[tuple[str, str], str], dict] = {}
    for item in _dedupe_results(results):
        name = str(getattr(item, "name", "") or "")
        url = str(getattr(item, "url", "") or "")
        issue = _issue_key(getattr(item, "issue", ""))
        if issue not in requested_issue_list:
            continue
        numbers = ",".join(str(number) for number in (getattr(item, "numbers", ()) or ()))
        key = (_site_key(name, url), issue)
        previous = incoming_success.get(key)
        if previous and previous["numbers"] != numbers:
            raise ValueError(f"缓存更新存在同站同期冲突：{name} {issue}期")
        incoming_success[key] = {"name": name, "url": url, "issue": issue, "numbers": numbers}

    incoming_failure: dict[tuple[tuple[str, str], str], dict] = {}
    for failure in failures or []:
        name = str(getattr(failure, "name", "") or "")
        url = str(getattr(failure, "url", "") or "")
        raw_issue = str(getattr(failure, "issue", "") or "").strip()
        failure_issues = [_issue_key(raw_issue)] if raw_issue else list(requested_issue_list)
        for issue in failure_issues:
            key = (_site_key(name, url), issue)
            value = {
                "name": name,
                "url": url,
                "issue": issue,
                "status": "failed",
                "reason": str(getattr(failure, "reason", "") or "未提供失败原因"),
            }
            previous = incoming_failure.get(key)
            if previous and previous["reason"] != value["reason"]:
                raise ValueError(f"缓存更新存在同站同期失败冲突：{name} {issue}期")
            incoming_failure[key] = value

    incoming_overlap = set(incoming_success) & set(incoming_failure)
    if incoming_overlap:
        raise ValueError(f"同一轮同站同期同时返回成功和失败：{sorted(incoming_overlap)!r}")

    latest_by_site: dict[tuple[str, str], tuple[str, int, int]] = {}
    for (site, cycle, issue), entry in timeline_by_period.items():
        current = latest_by_site.get(site)
        if current is None or entry["sequence"] > current[2]:
            latest_by_site[site] = (issue, cycle, entry["sequence"])

    run_sequence += 1
    incoming = [("success", value) for value in incoming_success.values()]
    incoming.extend(("failed", value) for value in incoming_failure.values())
    incoming.sort(key=lambda item: (item[1]["url"].lower(), item[1]["name"], int(item[1]["issue"])))

    for status, value in incoming:
        site = _site_key(value["name"], value["url"])
        last = latest_by_site.get(site)
        existing_periods = [
            (period_key, entry)
            for period_key, entry in timeline_by_period.items()
            if period_key[0] == site and period_key[2] == value["issue"]
        ]
        if len(existing_periods) > 1:
            raise ValueError(
                f"缓存同站可见期号跨周期冲突：{value['name']} {value['issue']}期"
            )
        if existing_periods:
            existing_key, existing_entry = existing_periods[0]
            cycle = existing_key[1]
            entry_sequence = int(existing_entry["sequence"])
        else:
            cycle = _infer_next_cycle(
                last[0] if last else None,
                last[1] if last else 0,
                value["issue"],
            )
            entry_sequence = run_sequence

        # Remove any old payload for this visible issue. Within the retained
        # ten-period window an issue cannot legitimately occur in two cycles.
        payload_key = (site, value["issue"])
        record_payload.pop(payload_key, None)
        failure_payload.pop(payload_key, None)
        for period_key in [key for key in timeline_by_period if key[0] == site and key[2] == value["issue"]]:
            timeline_by_period.pop(period_key, None)

        if status == "success":
            record_payload[payload_key] = value
        else:
            failure_payload[payload_key] = value
        timeline_by_period[(site, cycle, value["issue"])] = {
            "name": value["name"],
            "url": value["url"],
            "issue": value["issue"],
            "status": status,
            "sequence": entry_sequence,
            "cycle": cycle,
        }
        if not existing_periods:
            latest_by_site[site] = (value["issue"], cycle, entry_sequence)

    periods_by_site: dict[tuple[str, str], list[tuple[tuple[tuple[str, str], int, str], dict]]] = defaultdict(list)
    for key, entry in timeline_by_period.items():
        periods_by_site[key[0]].append((key, entry))
    retained_periods: set[tuple[tuple[str, str], int, str]] = set()
    for entries in periods_by_site.values():
        entries.sort(key=lambda item: (item[1]["sequence"], item[1]["cycle"], int(item[1]["issue"])))
        retained_periods.update(key for key, _entry in entries[-recent_count:])

    retained_timeline = [
        entry
        for key, entry in timeline_by_period.items()
        if key in retained_periods
    ]
    retained_timeline.sort(key=lambda entry: (entry["sequence"], entry["url"].lower(), entry["name"]))
    retained_payload_keys = {
        (_site_key(entry["name"], entry["url"]), entry["issue"])
        for entry in retained_timeline
    }

    combined_records: list[dict] = []
    combined_failures: list[dict] = []
    for entry in retained_timeline:
        key = (_site_key(entry["name"], entry["url"]), entry["issue"])
        if entry["status"] == "success":
            record = record_payload.get(key)
            if record is not None:
                combined_records.append(record)
        else:
            failure = failure_payload.get(key)
            if failure is not None:
                combined_failures.append(failure)

    atomic_write_json(
        path,
        {
            "version": CACHE_VERSION,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "recent_count": recent_count,
            "run_sequence": run_sequence,
            "records": combined_records,
            "failures": combined_failures,
            "timeline": retained_timeline,
        },
        trailing_newline=False,
    )
