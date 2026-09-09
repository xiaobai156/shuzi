import argparse
import os
import glob
import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from run_lock import exclusive_run_lock
from kill_numbers.domain.periods import (
    canonical_url, cycle_key, period_key, period_sort_key, split_period,
    are_consecutive, validate_cycle_lengths, target_identity, recent_periods,
    merge_cycle_lengths, target_cycle_lengths,
)
from kill_numbers.infrastructure.cache_repository import CACHE_VERSION, target_signature
from kill_numbers.validation.duplicate_gate import (
    DetectionStatus, EXIT_CODES, completeness_reasons, status_for_detection,
    normalize_numbers as normalize_number_string,
)
from kill_numbers.application.batch_service import iter_completed_batch
from kill_numbers.infrastructure.file_store import atomic_write_json, atomic_write_text
from kill_numbers.parsing.registry import ACQUISITION_ONLY_PARSERS
from kill_numbers.validation.result_validator import validate_crawl_results, evidence_from_source_document
from kill_numbers.acquisition.policy import target_policy


DEFAULT_OUTPUT = "重复检测结果.txt"
DEFAULT_CACHE = "recent_10_cache.json"
DEFAULT_RECENT = 10
DEFAULT_WORKERS = 8
SPECIAL_RECENT_COUNTS = {
    "葡京爆杀": 8,
    "摇钱树": 9,
    "首丘之思": 8,
}


@dataclass
class Record:
    source_file: str
    line_no: int
    numbers: str
    name: str
    issue: str
    raw: str
    url: str = ""
    cycle_id: str = ""
    target_id: str = ""


@dataclass
class CrawlProblem:
    name: str
    url: str
    reason: str


@dataclass
class RegionProblem:
    name: str
    url: str
    region: str
    reason: str


@dataclass
class TargetSnapshot:
    target: dict
    name: str
    url: str
    documents: list
    selected_content: str
    available_issues: list[str]


@dataclass
class SiteDuplicateMatch:
    name_a: str
    url_a: str
    name_b: str
    url_b: str
    status: str
    max_run_length: int
    issues: list[str]
    numbers_by_issue: list[tuple[str, str]]


def find_input_files(patterns: list[str]) -> list[Path]:
    files: list[Path] = []
    for pattern in patterns:
        matched = [Path(item) for item in glob.glob(pattern)]
        files.extend(path for path in matched if path.is_file())

    # 兼容旧版输出文件。
    fallback = Path("results.txt")
    if fallback.is_file():
        files.append(fallback)

    seen = set()
    unique_files = []
    for path in files:
        resolved = str(path.resolve()).lower()
        if resolved not in seen:
            seen.add(resolved)
            unique_files.append(path)
    return unique_files


def normalize_numbers(numbers: str) -> str:
    return normalize_number_string(numbers)


SUCCESS_FILE_RE = re.compile(
    r"(?:(?P<cycle>[1-9]\d{0,5})周期-)?(?P<issue>\d+)期-杀数字-成功\.txt"
)


def parse_line(
    line,
    source_file,
    line_no,
    default_issue=None,
    default_cycle="",
):
    raw = line.rstrip("\r\n")
    if not raw.strip():
        return None
    match = re.fullmatch(
        r"\s*([0-9０-９,，.．、\s]+)\s+([^0-9０-９\s].*?)(?:\s+(\d+\s*期))?\s*",
        raw,
    )
    if not match:
        return None
    numbers = normalize_numbers(match.group(1))
    explicit_issue = issue_key(match.group(3)) if match.group(3) else None
    if explicit_issue and default_issue and explicit_issue != issue_key(default_issue):
        raise ValueError("行内期号与文件名不一致")
    issue = explicit_issue or default_issue
    if not issue:
        raise ValueError("文件名和记录都缺少期数")
    return Record(
        source_file,
        line_no,
        numbers,
        match.group(2).strip(),
        f"{issue_key(issue)}期",
        raw,
        cycle_id=cycle_key(default_cycle),
    )


def _manifest_cycle_for_success_file(path: Path, issue: str) -> str:
    manifest_path = path.with_name(f"{issue}期-杀数字-运行.json")
    if not manifest_path.is_file():
        return ""
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if (
            not isinstance(value, dict)
            or value.get("version") != 1
            or value.get("outputs_finalized") is not True
            or issue_key(value.get("issue")) != issue
        ):
            raise ValueError("运行清单结构、状态或期数不匹配")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        matching = [
            item
            for item in value.get("files", [])
            if isinstance(item, dict)
            and Path(str(item.get("path") or "")).name == path.name
        ]
        if len(matching) != 1 or matching[0].get("sha256") != digest:
            raise ValueError("运行清单中的成功文件哈希不匹配")
        return cycle_key(value.get("cycle_id"))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"运行清单不能证明文件周期：{exc}") from exc


def read_records(files):
    records, bad_lines = [], []
    for path in files:
        name_match = SUCCESS_FILE_RE.fullmatch(path.name)
        default_issue = issue_key(name_match.group("issue")) if name_match else None
        filename_cycle = cycle_key(name_match.group("cycle")) if name_match and name_match.group("cycle") else ""
        manifest_cycle = ""
        if default_issue:
            try:
                manifest_cycle = _manifest_cycle_for_success_file(path, default_issue)
            except ValueError as exc:
                bad_lines.append(f"{path.name}：{exc}")
                continue
        if filename_cycle and manifest_cycle and filename_cycle != manifest_cycle:
            bad_lines.append(f"{path.name}：文件名周期与运行清单周期不一致")
            continue
        default_cycle = filename_cycle or manifest_cycle
        try:
            content = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            bad_lines.append(f"{path.name}：读取失败：{exc}")
            continue
        for line_no, line in enumerate(content.splitlines(), 1):
            try:
                record = parse_line(
                    line,
                    path.name,
                    line_no,
                    default_issue,
                    default_cycle,
                )
            except ValueError as exc:
                bad_lines.append(f"{path.name}:{line_no} {exc} {line}")
                continue
            if record:
                records.append(record)
            elif line.strip():
                bad_lines.append(f"{path.name}:{line_no} {line}")
    return records, bad_lines


def apply_default_cycle_to_unlabelled_records(records: list[Record], cycle: str) -> None:
    """Apply a CLI cycle only when the unlabelled file range cannot cross 1."""
    cycle = cycle_key(cycle)
    if not cycle:
        return
    unlabelled = [record for record in records if not record.cycle_id]
    if not unlabelled:
        return
    issues = sorted({int(issue_key(record.issue)) for record in unlabelled})
    if issues and issues != list(range(issues[0], issues[-1] + 1)):
        raise ValueError(
            "文件期数不连续，不能用一个 --cycle-id 覆盖；请使用周期文件名或运行清单"
        )
    for record in unlabelled:
        record.cycle_id = cycle


def record_period(record):
    return period_key(record.cycle_id, record.issue)


def duplicate_groups(records: list[Record]):
    groups: dict[tuple[str, str], list[Record]] = defaultdict(list)
    for record in records:
        groups[(record_period(record), record.numbers)].append(record)

    return {
        key: items
        for key, items in groups.items()
        if len({site_key(item) for item in items}) >= 2
    }


def cross_issue_groups(records: list[Record]):
    groups: dict[str, list[Record]] = defaultdict(list)
    for record in records:
        groups[record.numbers].append(record)

    return {
        numbers: items
        for numbers, items in groups.items()
        if len({record_period(item) for item in items}) >= 2
    }


def site_key(record: Record) -> tuple[str, str]:
    return ("id", record.target_id) if record.target_id else (record.name, canonical_url(record.url))


def same_site_issue_conflicts(records: list[Record]) -> list[tuple[str, str, str, str, str]]:
    """Return conflicting values instead of silently keeping the first one."""
    values: dict[tuple[tuple[str, str], str], str] = {}
    conflicts = []
    for record in records:
        issue = issue_key(record.issue)
        if not issue:
            continue
        key = (site_key(record), record_period(record))
        previous = values.get(key)
        if previous is not None and previous != record.numbers:
            conflicts.append(
                (record.name, record.url, issue, previous, record.numbers)
            )
        else:
            values[key] = record.numbers
    return conflicts


def record_conflict_problems(records: list[Record]) -> list[CrawlProblem]:
    return [
        CrawlProblem(
            name,
            url,
            f"{issue}期同站同期冲突，已拒绝判重和缓存写入：{first} | {second}",
        )
        for name, url, issue, first, second in same_site_issue_conflicts(records)
    ]


def multi_issue_completeness_problems(
    records: list[Record],
    requested_issues: list[str] | None,
) -> list[CrawlProblem]:
    requested = {issue_key(issue) for issue in (requested_issues or []) if issue_key(issue)}
    if len(requested) <= 1:
        return []

    by_site: dict[tuple[str, str], list[Record]] = defaultdict(list)
    for record in records:
        by_site[site_key(record)].append(record)

    problems = []
    for (name, url), site_records in by_site.items():
        found = {issue_key(record.issue) for record in site_records if issue_key(record.issue)}
        missing = sorted(requested - found, key=int)
        if missing:
            problems.append(
                CrawlProblem(
                    name,
                    url,
                    "指定多期数据不完整，缺少："
                    + ",".join(f"{issue}期" for issue in missing),
                )
            )
    return problems


def status_for_run(length: int) -> str:
    if length >= 6:
        return "reject"
    if length >= 3:
        return "suspect"
    return "ignore"


def matching_runs_by_issue(
    left_by_issue: dict[str, str],
    right_by_issue: dict[str, str],
    cycle_lengths: dict | None = None,
) -> list[list[tuple[str, str]]]:
    common_issues = sorted(
        set(left_by_issue) & set(right_by_issue),
        key=period_sort_key,
    )
    runs: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    previous_issue: int | None = None

    for issue in common_issues:
        numbers_match = left_by_issue[issue] == right_by_issue[issue]
        is_continuous = previous_issue is not None and are_consecutive(previous_issue, issue, cycle_lengths)

        if numbers_match and (not current or is_continuous):
            current.append((issue, left_by_issue[issue]))
        else:
            if current:
                runs.append(current)
            current = [(issue, left_by_issue[issue])] if numbers_match else []

        previous_issue = issue

    if current:
        runs.append(current)
    return runs


def site_duplicate_matches(records: list[Record], cycle_lengths=None) -> list[SiteDuplicateMatch]:
    conflicts = same_site_issue_conflicts(records)
    if conflicts:
        name, url, issue, first, second = conflicts[0]
        raise ValueError(
            f"{name} {issue}期同站同期冲突，已停止判重避免吞掉候选：{first} | {second}"
        )

    by_site: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    display: dict[tuple[str, str], tuple[str, str]] = {}

    for record in records:
        issue = issue_key(record.issue)
        if not issue:
            continue
        key = site_key(record)
        display[key] = (record.name, record.url)
        by_site[key][record_period(record)] = record.numbers

    matches: list[SiteDuplicateMatch] = []
    sites = sorted(by_site, key=lambda key: (key[0], key[1]))
    for left_index, left_key in enumerate(sites):
        for right_key in sites[left_index + 1:]:
            runs = matching_runs_by_issue(by_site[left_key], by_site[right_key], cycle_lengths)
            qualifying_runs = [run for run in runs if len(run) >= 3]
            if not qualifying_runs:
                continue

            best_run = max(
                qualifying_runs,
                key=lambda run: (len(run), period_sort_key(run[-1][0])),
            )
            left_name, left_url = display[left_key]
            right_name, right_url = display[right_key]
            matches.append(
                SiteDuplicateMatch(
                    name_a=left_name,
                    url_a=left_url,
                    name_b=right_name,
                    url_b=right_url,
                    status=status_for_run(len(best_run)),
                    max_run_length=len(best_run),
                    issues=[issue for issue, _numbers in best_run],
                    numbers_by_issue=best_run,
                )
            )

    return sorted(
        matches,
        key=lambda item: (
            0 if item.status == "reject" else 1,
            -item.max_run_length,
            item.name_a,
            item.name_b,
        ),
    )


def load_target_urls() -> list[dict]:
    try:
        import crawler
    except Exception:
        return []
    return list(getattr(crawler, "TARGETS", []))


def match_url(name: str, targets: list[dict]) -> str:
    exact = [
        target.get("url", "")
        for target in targets
        if target.get("name") == name
    ]
    if len(exact) == 1:
        return exact[0]

    return ""


def add_urls(records: list[Record]) -> None:
    targets = load_target_urls()
    for record in records:
        record.url = match_url(record.name, targets)


def parse_issues(raw: str) -> list[str]:
    issues = []
    for part in re.split(r"[,，\s]+", raw.strip()):
        if part:
            issues.append(str(int(part.replace("期", ""))))
    return issues


def issue_key(value: str) -> str:
    from kill_numbers.text_utils import normalize_issue
    try:
        return normalize_issue(value)
    except (TypeError, ValueError):
        return ""


def default_recent_issues(count: int) -> list[str]:
    import crawler

    default_issues = parse_issues(getattr(crawler, "DEFAULT_ISSUES", ""))
    if not default_issues:
        raise ValueError("crawler.py 里没有可用的 DEFAULT_ISSUES")

    last_issue = int(default_issues[-1])
    first_issue = max(1, last_issue - count + 1)
    return [str(issue) for issue in range(first_issue, last_issue + 1)]


def default_recent_issues_with_backup(count: int) -> list[str]:
    import crawler

    default_issues = parse_issues(getattr(crawler, "DEFAULT_ISSUES", ""))
    if not default_issues:
        raise ValueError("crawler.py 里没有可用的 DEFAULT_ISSUES")

    last_issue = int(default_issues[-1])
    first_issue = max(1, last_issue - count)
    return [str(issue) for issue in range(first_issue, last_issue + 1)]


def choose_issues_with_backup(records: list[Record], wanted_issues: list[str], limit: int) -> list[str]:
    records_by_issue = {issue_key(record.issue) for record in records}
    selected = [issue for issue in wanted_issues[-limit:] if issue in records_by_issue]

    for issue in reversed(wanted_issues[:-limit]):
        if len(selected) >= limit:
            break
        if issue in records_by_issue:
            selected.insert(0, issue)

    return selected


def filter_records_by_issues(records: list[Record], issues: list[str]) -> list[Record]:
    allowed = {issue_key(issue) for issue in issues}
    return [record for record in records if issue_key(record.issue) in allowed]


def recent_issues_from_latest(latest_issue: str, count: int) -> list[str]:
    last_issue = int(issue_key(latest_issue))
    first_issue = max(1, last_issue - count + 1)
    return [str(issue) for issue in range(first_issue, last_issue + 1)]


def target_name(target: dict) -> str:
    return str(target.get("name") or target.get("url") or "")


def region_config_problems(targets: list[dict]) -> list[RegionProblem]:
    import crawler

    problems: list[RegionProblem] = []
    for target in targets:
        raw_region = str(target.get("region") or "").strip()
        if not raw_region:
            problems.append(
                RegionProblem(
                    target_name(target),
                    target.get("url", ""),
                    raw_region,
                    "缺少 region；必须配置 top/bottom/上/下/顶部/尾部",
                )
            )
        elif not crawler.normalize_region(raw_region):
            problems.append(
                RegionProblem(
                    target_name(target),
                    target.get("url", ""),
                    raw_region,
                    "region 无法识别；只能使用 top/bottom/上/下/顶部/尾部",
                )
            )
    return problems


def fetch_target_content(target: dict) -> tuple[str, list]:
    import crawler
    return crawler.fetch_target_documents(target, [])


def available_issues_for_target(target: dict) -> tuple[str, str, list[str]]:
    import crawler

    name, documents = fetch_target_content(target)
    available, _selected = crawler.available_issues_for_documents(documents, target)
    return name or target_name(target), target.get("url", ""), available


def snapshot_for_target(target: dict, recent_count: int = DEFAULT_RECENT) -> TargetSnapshot:
    history_target = {
        **target,
        "_history_discovery": True,
        "_history_depth": recent_count,
    }
    with target_policy(history_target):
        return _snapshot_for_target(history_target)


def _snapshot_for_target(target: dict) -> TargetSnapshot:
    import crawler

    name, documents = fetch_target_content(target)
    available, selected_document = crawler.available_issues_for_documents(
        documents,
        target,
    )
    return TargetSnapshot(
        target=target,
        name=str(target.get("name") or "").strip() or name or target_name(target),
        url=target.get("url", ""),
        documents=documents,
        selected_content=(
            selected_document.content
            if selected_document is not None
            else crawler.document_debug_text(documents)
        ),
        available_issues=[issue_key(issue) for issue in available if issue_key(issue)],
    )


def latest_issue_from_snapshots(snapshots: list[TargetSnapshot]) -> str | None:
    all_issues = {
        issue_key(issue)
        for snapshot in snapshots
        for issue in snapshot.available_issues
        if issue_key(issue)
    }
    if not all_issues:
        return None
    return str(max(int(issue) for issue in all_issues))


def selected_issues_from_snapshots(
    snapshots: list[TargetSnapshot],
    latest_issue: str,
    recent_count: int,
) -> list[str]:
    selected = set(recent_issues_from_latest(latest_issue, recent_count))
    for snapshot in snapshots:
        available = sorted(
            {issue_key(issue) for issue in snapshot.available_issues if issue_key(issue)},
            key=lambda value: int(value),
        )
        selected.update(available[-recent_count:])
    return sorted(selected, key=lambda value: int(value))


def recent_issues_for_snapshot(snapshot: TargetSnapshot, recent_count: int) -> list[str]:
    import crawler

    available = []
    seen = set()
    for issue in snapshot.available_issues:
        key = issue_key(issue)
        if key and key not in seen:
            seen.add(key)
            available.append(key)

    region = crawler.normalize_region(snapshot.target.get("region"))
    if region == "top":
        return available[:recent_count]
    return available[-recent_count:]


def recent_count_for_snapshot(snapshot, fallback_recent_count):
    # Historical eight/nine-period exceptions cannot certify a ten-period check.
    return fallback_recent_count


def fetch_target_snapshots(
    workers: int,
    recent_count: int = DEFAULT_RECENT,
) -> tuple[list[TargetSnapshot], list[CrawlProblem], list[RegionProblem], str | None]:
    import crawler

    targets = list(getattr(crawler, "TARGETS", []))
    region_problems = region_config_problems(targets)
    snapshots: list[TargetSnapshot] = []
    problems: list[CrawlProblem] = []
    max_workers = max(1, min(workers, len(targets) or 1))

    print(f"自动探测最新期：读取目标 {len(targets)} 个，并发 {max_workers}")
    for done_count, entry in enumerate(
        iter_completed_batch(
            targets,
            lambda target: snapshot_for_target(target, recent_count),
            max_workers,
        ),
        start=1,
    ):
        target = entry.item
        url = target.get("url", "")
        fallback_name = target_name(target)
        if entry.error is not None:
            problems.append(CrawlProblem(fallback_name, url, f"探测最新期失败：{entry.error}"))
            print(f"[{done_count}/{len(targets)}] 最新期探测失败：{url}")
            continue

        snapshot = entry.result
        if snapshot is None:
            problems.append(CrawlProblem(fallback_name, url, "探测最新期失败：没有返回快照"))
            print(f"[{done_count}/{len(targets)}] 最新期探测失败：{url}")
            continue
        snapshots.append(snapshot)
        print(f"[{done_count}/{len(targets)}] 最新期探测：{url} ({len(snapshot.available_issues)} 期)")

    latest_issue = latest_issue_from_snapshots(snapshots)
    if not latest_issue:
        problems.append(CrawlProblem("全部目标", "", "没有从实际抓取数据中识别到任何可用期数"))

    return snapshots, problems, region_problems, latest_issue


def discover_latest_issue(workers: int) -> tuple[str | None, list[CrawlProblem], list[RegionProblem]]:
    import crawler

    targets = list(getattr(crawler, "TARGETS", []))
    region_problems = region_config_problems(targets)
    problems: list[CrawlProblem] = []
    all_issues: set[str] = set()
    max_workers = max(1, min(workers, len(targets) or 1))

    print(f"自动探测最新期：读取目标 {len(targets)} 个，并发 {max_workers}")
    for done_count, entry in enumerate(
        iter_completed_batch(targets, available_issues_for_target, max_workers),
        start=1,
    ):
        target = entry.item
        url = target.get("url", "")
        fallback_name = target_name(target)
        if entry.error is not None:
            problems.append(CrawlProblem(fallback_name, url, f"探测最新期失败：{entry.error}"))
            print(f"[{done_count}/{len(targets)}] 最新期探测失败：{url}")
            continue

        result = entry.result
        if result is None:
            problems.append(CrawlProblem(fallback_name, url, "探测最新期失败：没有返回期数"))
            print(f"[{done_count}/{len(targets)}] 最新期探测失败：{url}")
            continue
        name, _url, available = result
        normalized = [issue_key(issue) for issue in available if issue_key(issue)]
        all_issues.update(normalized)
        print(f"[{done_count}/{len(targets)}] 最新期探测：{url} ({len(normalized)} 期)")

    if not all_issues:
        problems.append(CrawlProblem("全部目标", "", "没有从实际抓取数据中识别到任何可用期数"))
        return None, problems, region_problems

    return str(max(int(issue) for issue in all_issues)), problems, region_problems


def records_from_snapshot(snapshot: TargetSnapshot, issues: list[str]) -> tuple[list[Record], CrawlProblem | None]:
    try:
        with target_policy(snapshot.target, issues):
            return _records_from_snapshot(snapshot, issues)
    except Exception as exc:
        return [], CrawlProblem(snapshot.name, snapshot.url, str(exc))


def _records_from_snapshot(snapshot: TargetSnapshot, issues: list[str]) -> tuple[list[Record], CrawlProblem | None]:
    import crawler

    target = snapshot.target
    if target.get("special_parser") in ACQUISITION_ONLY_PARSERS:
        results, failure = crawler.crawl_one(target, issues)
        accepted, reason = validate_crawl_results(target, issues, results, failure)
        if reason:
            return [], CrawlProblem(snapshot.name, snapshot.url, reason)
        return [
            Record(
                source_file="实时链式采集",
                line_no=0,
                numbers=normalize_numbers(",".join(result.numbers)),
                name=result.name,
                issue=f"{issue_key(result.issue)}期",
                raw="",
                url=result.url,
                cycle_id=cycle_key(target.get("cycle_id")),
                target_id=target_identity(target),
            )
            for result in accepted
        ], None

    parsed = crawler.parse_target_document_results(
        snapshot.documents,
        target,
        issues,
    )
    issue_map = parsed.issue_map
    selected_document = parsed.primary_document
    selected_documents = parsed.source_documents
    if issue_map:
        issue_map = crawler.validate_issue_map(
            target,
            issues,
            issue_map,
            source_document=selected_document,
            source_documents=selected_documents,
            require_source_document=True,
        )
    content = (
        selected_document.content
        if selected_document is not None
        else snapshot.selected_content
    )
    if issue_map:
        for issue, numbers in issue_map.items():
            evidence_from_source_document(
                target,
                issue,
                numbers,
                selected_documents[issue],
            )
        return [
            Record(
                source_file="实时抓取缓存",
                line_no=0,
                numbers=normalize_numbers(",".join(numbers)),
                name=snapshot.name,
                issue=f"{issue}期",
                raw="",
                url=snapshot.url,
                cycle_id=cycle_key(target.get("cycle_id")),
                target_id=target_identity(target),
            )
            for issue, numbers in issue_map.items()
        ], None

    mismatch = crawler.diagnose_issue_mismatch(
        content,
        issues,
        keywords=target.get("keywords"),
        expected_count=target.get("count"),
        allow_duplicate_numbers=target.get("allow_duplicate_numbers", False),
        region=target.get("region"),
        anchor=target.get("anchor"),
        stop_anchor=target.get("stop_anchor"),
        issue_position_window=target.get("issue_position_window"),
    )
    wanted = ",".join(f"{issue}期" for issue in issues)
    if mismatch:
        detail = "；".join(
            f"{issue}期：{message}" for issue, message in mismatch.items()
        )
        reason = f"没有找到符合配置的 {wanted} 号码；{detail}"
    elif snapshot.available_issues:
        found_text = ",".join(
            f"{issue}期" for issue in crawler.nearest_issues(snapshot.available_issues, issues)
        )
        reason = f"没有找到指定期数 {wanted} 的号码；本页同栏目找到：{found_text}"
    else:
        reason = f"没有找到指定期数 {wanted} 的号码；本页同栏目没有识别到可用期数"

    debug_path = crawler.save_debug_page(target, issues, snapshot.name, content, reason)
    if debug_path:
        reason = f"{reason}；调试页面：{debug_path.name}"
    return [], CrawlProblem(snapshot.name, snapshot.url, reason)


def records_from_snapshots(
    snapshots: list[TargetSnapshot],
    issues: list[str],
) -> tuple[list[Record], list[CrawlProblem]]:
    records: list[Record] = []
    problems: list[CrawlProblem] = []

    print(f"检测期数：{','.join(issue + '期' for issue in issues)}")
    print(f"复用首次抓取缓存：{len(snapshots)} 个目标")

    for done_count, snapshot in enumerate(snapshots, start=1):
        found, failure = records_from_snapshot(snapshot, issues)
        if found:
            records.extend(found)
            print(f"[{done_count}/{len(snapshots)}] 成功：{snapshot.url} ({len(found)} 条)")
        else:
            reason = failure.reason if failure else "没有返回数据"
            problems.append(failure or CrawlProblem(snapshot.name, snapshot.url, reason))
            print(f"[{done_count}/{len(snapshots)}] 无数据：{snapshot.url}")

    return records, problems


def records_from_recent_snapshots(
    snapshots: list[TargetSnapshot],
    recent_count: int,
) -> tuple[list[Record], list[CrawlProblem], list[str]]:
    records: list[Record] = []
    problems: list[CrawlProblem] = []
    used_issues: set[str] = set()

    print(f"按各站点自己的 region 最新期取近 {recent_count} 期")
    print(f"复用首次抓取缓存：{len(snapshots)} 个目标")

    for done_count, snapshot in enumerate(snapshots, start=1):
        site_recent_count = recent_count_for_snapshot(snapshot, recent_count)
        issues = recent_issues_for_snapshot(snapshot, site_recent_count)
        if not issues:
            problems.append(CrawlProblem(snapshot.name, snapshot.url, "本页同栏目没有识别到可用期数"))
            print(f"[{done_count}/{len(snapshots)}] 无数据：{snapshot.url}")
            continue

        found, failure = records_from_snapshot(snapshot, issues)
        if found:
            records.extend(found)
            used_issues.update(issue_key(item.issue) for item in found if issue_key(item.issue))
            print(f"[{done_count}/{len(snapshots)}] 成功：{snapshot.url} ({len(found)} 条)")
        else:
            reason = failure.reason if failure else "没有返回数据"
            problems.append(failure or CrawlProblem(snapshot.name, snapshot.url, reason))
            print(f"[{done_count}/{len(snapshots)}] 无数据：{snapshot.url}")

    return records, problems, sorted(used_issues, key=lambda value: int(value))


def cache_site_key(name: str, url: str) -> tuple[str, str]:
    return ("url", url.strip()) if url.strip() else ("name", name.strip())


def validate_cache_data(data, path, expected_recent_count=None, targets=None):
    if not isinstance(data, dict) or type(data.get("version")) is not int or data.get("version") not in (1, CACHE_VERSION):
        raise ValueError(f"缓存文件结构或版本错误：{path}")
    if not isinstance(data.get("generated_at"), str) or not data["generated_at"].strip():
        raise ValueError("缓存文件 generated_at 无效")
    count = data.get("recent_count")
    if type(count) is not int or count <= 0 or (expected_recent_count is not None and count != expected_recent_count):
        raise ValueError("缓存 recent_count 不匹配或无效")
    records = data.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("缓存文件 records 缺失或为空")
    failures = data.get("failures", [])
    if not isinstance(failures, list):
        raise ValueError("缓存失败状态格式错误")
    failure_keys = set()
    for item in failures:
        if (not isinstance(item, dict) or not isinstance(item.get("name"), str)
            or not item["name"].strip() or not isinstance(item.get("url"), str)
            or not item["url"].strip() or item.get("status") != "failed"
            or not isinstance(item.get("reason"), str) or not item["reason"].strip()
            or not issue_key(item.get("issue")) or "numbers" in item):
            raise ValueError("缓存失败状态无效")
        failure_keys.add((item.get('target_id') or (item['name'], canonical_url(item['url'])), period_key(item.get("cycle_id"), item["issue"])))
    values, loaded = {}, []
    for item in records:
        if (not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip()
            or not isinstance(item.get("url"), str) or not item["url"].strip() or not issue_key(item.get("issue"))):
            raise ValueError("缓存记录字段无效")
        numbers = normalize_numbers(item.get("numbers"))
        cycle = cycle_key(item.get("cycle_id"))
        key = (item.get('target_id') or (item['name'], canonical_url(item['url'])), period_key(cycle, item['issue']))
        if key in failure_keys:
            raise ValueError("缓存同站同期同时存在成功和失败")
        if key in values and values[key] != numbers:
            raise ValueError("缓存同站同期冲突")
        if key in values:
            continue
        values[key] = numbers
        loaded.append(Record(str(path), 0, numbers, item['name'], str(item['issue']), '', item['url'], cycle, str(item.get('target_id') or '')))
    lengths = validate_cycle_lengths(data.get("cycle_lengths", {}))
    if targets is not None:
        if data['version'] != CACHE_VERSION:
            raise ValueError("检测未完成：版本1没有周期/配置证据，需要重建版本2缓存")

        sites = data.get('sites', {})
        if not isinstance(sites, dict):
            raise ValueError("检测未完成：sites 元数据无效")
        for target in targets:
            meta = sites.get(target_identity(target), {})
            if not isinstance(meta, dict) or meta.get('contract_hash') != target_signature(target):
                raise ValueError(f"检测未完成：{target.get('name')} 缓存配置证据缺失或过期")
            latest = meta.get('latest_period')
            if not latest:
                raise ValueError('检测未完成：缺少每站最新期证据')
            expected = set(recent_periods(latest, 10, lengths))
            relevant_failures = [f for f in failures if f.get('target_id') == target_identity(target)
                and (period_key(f.get('cycle_id'), f['issue']) in expected
                     or period_sort_key(period_key(f.get('cycle_id'), f['issue'])) > period_sort_key(latest))]
            if relevant_failures:
                raise ValueError('检测未完成：要求范围内或较新期存在失败状态')
            if meta.get('cycle_verified') is not True:
                raise ValueError(f"检测未完成：{target.get('name')} 周期未确认")
        reasons = completeness_reasons(loaded, targets, count, cycle_lengths=lengths,
            latest_by_site={url: meta['latest_period'] for url, meta in sites.items() if isinstance(meta,dict) and meta.get('latest_period')})
        if reasons:
            raise ValueError('检测未完成：'+'；'.join(f'{name} {reason}' for name,_url,reason in reasons))
    return records


def effective_targets_with_cycle_contract(
    raw_targets: list[dict],
    cycle: str = "",
    cycle_lengths: dict | None = None,
) -> tuple[list[dict], dict[str, int]]:
    """Apply a CLI cycle without overriding contradictory target contracts."""
    cli_cycle = cycle_key(cycle)
    cli_lengths = validate_cycle_lengths(cycle_lengths or {})
    global_lengths = dict(cli_lengths)
    effective: list[dict] = []
    for raw_target in raw_targets:
        target = dict(raw_target)
        configured_cycle = cycle_key(target.get("cycle_id"))
        if cli_cycle and configured_cycle and cli_cycle != configured_cycle:
            raise ValueError(
                f"{target.get('name') or target.get('url')} 的 cycle_id "
                f"{configured_cycle} 与命令行 {cli_cycle} 冲突"
            )
        selected_cycle = cli_cycle or configured_cycle
        if selected_cycle:
            target["cycle_id"] = selected_cycle
        target_lengths = target_cycle_lengths(target)
        merged_target_lengths = merge_cycle_lengths(target_lengths, cli_lengths)
        if merged_target_lengths:
            target["cycle_lengths"] = merged_target_lengths
        global_lengths = merge_cycle_lengths(global_lengths, target_lengths)
        effective.append(target)
    return effective, global_lengths


def write_records_cache(
    path: Path,
    records: list[Record],
    issues: list[str],
    recent_count: int,
    problems: list[CrawlProblem] | None = None,
    region_problems: list[RegionProblem] | None = None,
    targets: list[dict] | None = None,
) -> None:
    if problems:
        raise ValueError(f"存在 {len(problems)} 个抓取/识别异常，拒绝覆盖正式缓存")
    if region_problems:
        raise ValueError(f"存在 {len(region_problems)} 个 region 配置异常，拒绝覆盖正式缓存")

    data = {
        "version": CACHE_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sites": {target_identity(t): {'contract_hash': target_signature(t),
            'cycle_verified': bool(t.get('cycle_id')), 'cycle_id': cycle_key(t.get('cycle_id')),
            'latest_period': max((record_period(r) for r in records if r.target_id == target_identity(t)), key=period_sort_key, default='') }
            for t in (targets or [])},
        "cycle_lengths": merge_cycle_lengths(
            *(target_cycle_lengths(target) for target in (targets or []))
        ),
        "recent_count": recent_count,
        "records": [
            {
                "name": record.name,
                "url": record.url,
                "issue": issue_key(record.issue),
                "numbers": record.numbers,
                "cycle_id": record.cycle_id,
                "target_id": record.target_id,
            }
            for record in records
        ],
        "failures": [],
    }
    validate_cache_data(data, path, expected_recent_count=recent_count, targets=targets)

    atomic_write_json(path, data, trailing_newline=False)


def load_records_cache(
    path: Path,
    expected_recent_count: int | None = None,
    targets: list[dict] | None = None,
) -> list[Record]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"缓存文件无法读取：{path}；{exc}") from exc
    records = validate_cache_data(data, path, expected_recent_count, targets)

    loaded: list[Record] = []
    for item in records:
        issue = issue_key(item.get("issue", ""))
        numbers = normalize_numbers(str(item.get("numbers", "")))
        name = str(item.get("name") or "")
        loaded.append(
            Record(
                source_file=path.name,
                line_no=0,
                numbers=numbers,
                name=name,
                issue=f"{issue}期",
                raw="",
                url=str(item.get("url") or ""),
                cycle_id=cycle_key(item.get("cycle_id")),
                target_id=str(item.get("target_id") or ""),
            )
        )
    return loaded


def records_from_crawler(issues: list[str], workers: int) -> tuple[list[Record], list[CrawlProblem]]:
    import crawler

    targets = list(getattr(crawler, "TARGETS", []))
    records: list[Record] = []
    problems: list[CrawlProblem] = []
    max_workers = max(1, min(workers, len(targets) or 1))

    print(f"检测期数：{','.join(issue + '期' for issue in issues)}")
    print(f"读取目标：{len(targets)} 个，并发：{max_workers}")

    for done_count, entry in enumerate(
        iter_completed_batch(
            targets,
            lambda target: crawler.crawl_one(target, issues),
            max_workers,
        ),
        start=1,
    ):
        target = entry.item
        url = target.get("url", "")
        target_name = target.get("name") or url
        if entry.error is not None:
            problems.append(CrawlProblem(target_name, url, str(entry.error)))
            print(f"[{done_count}/{len(targets)}] 失败：{url}")
            continue

        found, failure = entry.result or ([], None)
        if found:
            for item in found:
                records.append(
                    Record(
                        source_file="实时抓取",
                        line_no=0,
                        numbers=normalize_numbers(",".join(item.numbers)),
                        name=item.name,
                        issue=f"{item.issue}期",
                        raw="",
                        url=item.url,
                        cycle_id=cycle_key(target.get("cycle_id")),
                        target_id=target_identity(target),
                    )
                )
            print(f"[{done_count}/{len(targets)}] 成功：{url} ({len(found)} 条)")
        else:
            reason = failure.reason if failure else "没有返回数据"
            problems.append(CrawlProblem(target_name, url, reason))
            print(f"[{done_count}/{len(targets)}] 无数据：{url}")

    return records, problems


def write_report(output, files, records, duplicates, cross_duplicates, bad_lines, problems, issues=None):
    """Legacy API is diagnostic only; use the same fail-closed report writer."""
    problems = list(problems) + [CrawlProblem('全部目标', '', '旧报告入口没有完整性证明，检测未完成')]
    write_report_v2(output, files, records, duplicates, cross_duplicates, [], bad_lines, problems, issues=issues)


def format_issue(issue: str) -> str:
    cycle, value = split_period(issue)
    return f"{cycle}周期/{value}期" if cycle else f"{value}期"


def site_match_status_label(status: str) -> str:
    if status == "reject":
        return "重复网站，直接拒收"
    if status == "suspect":
        return "疑似重复，交给人工审核"
    return "不处理"


def write_report_v2(output, files, records, duplicates, cross_duplicates, site_matches,
                    bad_lines, problems, region_problems=None, issues=None, latest_issue=None,
                    status=None):
    status = status or status_for_detection(problems, region_problems, bad_lines, records, site_matches)
    lines = ["重复检测结果", f"检测状态：{status.value}",
             "规则：相同周期、相同期号、原始号码整串顺序相同才累计；连续3-5期疑似，6期及以上拒收。",
             f"有效读取记录：{len(records)} 条", ""]
    if status == DetectionStatus.INCOMPLETE:
        lines += ["检测未完成，本轮不生成准入结论。以下局部匹配只作为核查线索。", ""]
    elif status == DetectionStatus.CLEAN:
        lines += ["所有启用目标已通过连续近10期完整性校验；未达到连续3期重复门槛。", ""]
    for item in site_matches:
        lines += [f"{'拒收证据' if item.status == 'reject' else '疑似证据'}：{item.name_a} / {item.name_b} 连续 {item.max_run_length} 期",
                  f"网址A：{item.url_a}", f"网址B：{item.url_b}"]
        lines += [f"  {format_issue(issue)}：{numbers}" for issue,numbers in item.numbers_by_issue]
        lines.append("")
    if duplicates:
        lines.append("已读取数据中的同期整串匹配：")
        for (issue, numbers), items in sorted(duplicates.items(), key=lambda item:period_sort_key(item[0][0])):
            lines.append(f"{format_issue(issue)} {numbers}："+'、'.join(item.name for item in items))
    if cross_duplicates:
        lines += ["", f"跨期整串匹配：{len(cross_duplicates)} 组（不直接作为同期拒收依据）"]
    if problems or region_problems or bad_lines:
        lines += ["", "未完成原因："]
        lines += [f"{item.name} {item.url}：{item.reason}" for item in [*(problems or []),*(region_problems or [])]]
        lines += [f"未识别行：{line}" for line in bad_lines]
    atomic_write_text(output, "\n".join(lines)+"\n")


def _main_unlocked() -> int:
    import crawler
    parser = argparse.ArgumentParser(description="完整近10期判重；资料不足只能报告检测未完成")
    parser.add_argument("files", nargs="*")
    parser.add_argument("--issues")
    parser.add_argument("--latest")
    parser.add_argument("--recent", type=int, default=10)
    parser.add_argument("--workers", type=int, default=8)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--from-files", action="store_true")
    modes.add_argument("--from-cache", action="store_true")
    modes.add_argument(
        "--rebuild-history",
        action="store_true",
        help="从每个站点已确认栏目重建近10期；必须明确提供 cycle-id",
    )
    parser.add_argument("--write-cache", action="store_true")
    parser.add_argument("--cache", default=str(crawler.CACHE_FILE))
    parser.add_argument("--output", default=str(crawler.SCRIPT_DIR / DEFAULT_OUTPUT))
    parser.add_argument("--cycle-id", default=os.environ.get("SHUZI_CYCLE_ID", ""))
    parser.add_argument("--previous-cycle-length", type=int, default=None)
    args = parser.parse_args()
    if args.issues and args.latest:
        parser.error("--issues 和 --latest 只能二选一")
    if args.recent < 1 or args.workers < 1:
        parser.error("recent 和 workers 必须是正整数")
    if args.from_cache and (args.files or args.write_cache or args.issues or args.latest):
        parser.error("缓存读取不能与文件、期数或写缓存模式混用")
    if args.rebuild_history and (args.files or args.issues or args.latest):
        parser.error("--rebuild-history 不能与文件、--issues 或 --latest 混用")
    if args.from_files and args.latest:
        parser.error("文件模式不能使用 --latest；请从文件名或运行清单取得期数")
    try:
        cycle = cycle_key(args.cycle_id)
        command_lengths = {}
        if args.previous_cycle_length is not None:
            if not cycle or int(cycle) <= 1:
                raise ValueError("上一周期长度必须同时提供明确的 --cycle-id")
            command_lengths = validate_cycle_lengths(
                {str(int(cycle) - 1): args.previous_cycle_length}
            )
        targets, lengths = effective_targets_with_cycle_contract(
            crawler.load_targets(),
            cycle,
            command_lengths,
        )
    except ValueError as exc:
        parser.error(str(exc))
    live_history_mode = args.rebuild_history or not (
        args.from_cache
        or args.files
        or args.from_files
        or args.issues
        or args.latest
    )
    if live_history_mode and not cycle:
        parser.error(
            "实时重建近10期必须明确 --cycle-id（或先设置 SHUZI_CYCLE_ID）；"
            "只读已积累缓存请使用 --from-cache"
        )
    original_targets = crawler.TARGETS
    crawler.TARGETS = targets
    records, files, bad_lines, problems = [], [], [], []
    region_problems = region_config_problems(targets)
    issues, latest_issue = None, None
    try:
        if args.from_cache:
            cache_path = Path(args.cache)
            records = load_records_cache(cache_path, args.recent, targets)
            cache_data = json.loads(cache_path.read_text(encoding="utf-8-sig"))
            cache_lengths = validate_cycle_lengths(cache_data.get('cycle_lengths', {}))
            for cycle_name, count in cache_lengths.items():
                if cycle_name in lengths and lengths[cycle_name] != count:
                    raise ValueError("缓存周期长度与命令行声明冲突")
                lengths[cycle_name] = count
            health_path = getattr(crawler, 'CACHE_STATE_FILE', None)
            if health_path and Path(health_path).exists():
                health = json.loads(Path(health_path).read_text(encoding='utf-8'))
                if not isinstance(health, dict) or health.get('cache_updated') is not True:
                    problems.append(CrawlProblem('全部目标', '', '最近单期运行未完成缓存同步，不能把旧缓存当成本轮状态'))
        elif args.files or args.from_files:
            files = [Path(f) for f in args.files] if args.files else sorted(crawler.RESULTS_DIR.glob('*期-杀数字-成功.txt'))
            records, bad_lines = read_records(files)
            add_urls(records)
            apply_default_cycle_to_unlabelled_records(records, cycle)
            if args.issues:
                issues = parse_issues(args.issues)
                records = filter_records_by_issues(records, issues)
        elif args.issues or args.latest:
            issues = parse_issues(args.issues) if args.issues else recent_issues_from_latest(args.latest, args.recent)
            records, problems = records_from_crawler(issues, args.workers)
            problems.append(CrawlProblem('全部目标', '', '统一指定期数仅供诊断，未证明各站点自己的最新期'))
        else:
            snapshots, discovery_problems, region_problems, latest_issue = fetch_target_snapshots(
                args.workers, args.recent
            )
            problems.extend(discovery_problems)
            records, crawl_problems, issues = records_from_recent_snapshots(snapshots, args.recent)
            problems.extend(crawl_problems)
    except (OSError, ValueError, RuntimeError) as exc:
        problems.append(CrawlProblem('检测入口', '', str(exc)))
    finally:
        crawler.TARGETS = original_targets
    problems.extend(CrawlProblem(*r) for r in completeness_reasons(records, targets, args.recent, cycle_lengths=lengths))
    conflicts = record_conflict_problems(records)
    problems.extend(conflicts)
    duplicates = duplicate_groups(records)
    cross_duplicates = cross_issue_groups(records)
    matches = [] if conflicts else site_duplicate_matches(records, lengths)
    status = status_for_detection(problems, region_problems, bad_lines, records, matches)
    if args.write_cache and status != DetectionStatus.INCOMPLETE:
        try:
            write_records_cache(Path(args.cache), records, issues or [], args.recent, targets=targets)
        except (OSError, ValueError) as exc:
            problems.append(CrawlProblem('缓存更新', '', str(exc)))
            status = DetectionStatus.INCOMPLETE
    write_report_v2(Path(args.output), files, records, duplicates, cross_duplicates, matches,
                    bad_lines, problems, region_problems, issues, latest_issue, status=status)
    print(f"{status.value}：读取 {len(records)} 条，匹配风险 {len(matches)} 组；报告：{args.output}")
    return EXIT_CODES[status]


def main() -> int:
    try:
        with exclusive_run_lock(Path(__file__).resolve().parent / ".crawler-and-duplicates.lock"):
            return _main_unlocked()
    except (RuntimeError, OSError, ValueError) as exc:
        print(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
