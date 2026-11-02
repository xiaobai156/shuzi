import argparse
import glob
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from run_lock import exclusive_run_lock
from kill_numbers.application.batch_service import iter_completed_batch
from kill_numbers.domain.models import SourceDocument
from kill_numbers.infrastructure.cache_format import (
    build_cache_document,
    failures_from_cache_document,
    records_from_cache_document,
    target_contract_fingerprint,
)
from kill_numbers.infrastructure.file_store import atomic_write_json, atomic_write_text
from kill_numbers.parsing.common import target_issue_region
from kill_numbers.parsing.registry import ACQUISITION_ONLY_PARSERS
from kill_numbers.validation.result_validator import validate_crawl_results, validate_issue_map


DEFAULT_OUTPUT = "重复检测结果.txt"
DEFAULT_CACHE = "recent_10_cache.json"
DEFAULT_RECENT = 10
DEFAULT_WORKERS = 8
@dataclass
class Record:
    source_file: str
    line_no: int
    numbers: str
    name: str
    issue: str
    raw: str
    url: str = ""


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
    # 不排序，只清理分隔符周围空格，保留原始顺序。
    numbers = numbers.strip().replace("，", ",").replace("．", ".")
    numbers = re.sub(r"\s*,\s*", ",", numbers)
    numbers = re.sub(r"\s*\.\s*", ".", numbers)
    return numbers


def parse_line(line: str, source_file: str, line_no: int) -> Record | None:
    raw = line.rstrip("\n")
    if not raw.strip():
        return None

    default = re.fullmatch(r"(\d+)期-杀数字-成功\.txt", Path(source_file).name)
    match = re.match(r"^\s*([0-9０-９,，.．\s]+?)\s+(.+?)\s+(\d+\s*期)\s*$", raw)
    if not match and default:
        match = re.match(r"^\s*([0-9０-９,，.．\s]+?)\s+(.+?)\s+(\d+\s*期)\s*$", raw + ' ' + default.group(1) + '期')
    if not match:
        return None

    numbers = normalize_numbers(match.group(1))
    name = re.sub(r"\s+", " ", match.group(2).strip())
    issue = re.sub(r"\s+", "", match.group(3))
    if default and int(default.group(1)) != int(issue[:-1]):
        return None
    return Record(source_file, line_no, numbers, name, issue, raw)


def read_records(files: list[Path]) -> tuple[list[Record], list[str]]:
    records: list[Record] = []
    bad_lines: list[str] = []
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except UnicodeDecodeError:
            lines = path.read_text(encoding="gb18030", errors="ignore").splitlines()

        for line_no, line in enumerate(lines, start=1):
            record = parse_line(line, path.name, line_no)
            if record:
                records.append(record)
            elif line.strip():
                bad_lines.append(f"{path.name}:{line_no} {line}")
    return records, bad_lines


def duplicate_groups(records: list[Record]):
    groups: dict[tuple[str, str], list[Record]] = defaultdict(list)
    for record in records:
        groups[(record.issue, record.numbers)].append(record)

    return {
        key: items
        for key, items in groups.items()
        if len(items) >= 2
    }


def cross_issue_groups(records: list[Record]):
    groups: dict[str, list[Record]] = defaultdict(list)
    for record in records:
        groups[record.numbers].append(record)

    return {
        numbers: items
        for numbers, items in groups.items()
        if len({item.issue for item in items}) >= 2
    }


def site_key(record: Record) -> tuple[str, str]:
    return record.name, record.url


def same_site_issue_conflicts(records: list[Record]) -> list[tuple[str, str, str, str, str]]:
    """Return conflicting values instead of silently keeping the first one."""
    values: dict[tuple[tuple[str, str], str], str] = {}
    conflicts = []
    for record in records:
        issue = issue_key(record.issue)
        if not issue:
            continue
        key = (site_key(record), issue)
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


def duplicate_readiness_problems(records, recent_count=10, targets=None):
    """Gate conclusions, not cache deserialization or partial diagnostic results."""
    by_site = defaultdict(list)
    for record in records:
        by_site[site_key(record)].append(record)
    contracts = { (target_name(t), str(t.get('url') or '')): t for t in targets or [] }
    problems = []
    if len(by_site) < 2:
        problems.append(CrawlProblem('判重范围', '', '检测未完成：不足两个可比较站点'))
    for site in set(by_site) | set(contracts):
        t = contracts.get(site)
        required = (t or {}).get('recent_history_count') or recent_count
        if isinstance(required, bool) or not isinstance(required, int) or required < 1:
            problems.append(CrawlProblem(*site, '检测未完成：历史期数配置无效'))
            continue
        periods = {int(issue_key(r.issue)) for r in by_site[site] if issue_key(r.issue)}
        if not periods or not set(range(max(periods)-required+1, max(periods)+1)).issubset(periods):
            problems.append(CrawlProblem(*site, f'检测未完成：缺少连续近{required}期完整数据'))
        if targets is not None and (t is None or t.get('disabled')):
            problems.append(CrawlProblem(*site, '检测未完成：站点身份未匹配启用配置'))
        for record in by_site[site]:
            tokens = re.split(r'[,\.\s]+', record.numbers.strip())
            validation_target = t or {'count': len(tokens)}
            exception = validation_target.get('cache_issue_exceptions', {}).get(issue_key(record.issue))
            if isinstance(exception, dict) and exception.get('approved') is True:
                validation_target = {**validation_target, 'count': exception['count']}
            try:
                validate_issue_map(validation_target, [issue_key(record.issue)], {issue_key(record.issue): tokens})
            except ValueError as exc:
                problems.append(CrawlProblem(*site, f'检测未完成：{exc}'))
    sites = list(by_site)
    for index, left in enumerate(sites):
        left_periods = {issue_key(r.issue) for r in by_site[left]}
        for right in sites[index+1:]:
            if not left_periods.intersection(issue_key(r.issue) for r in by_site[right]):
                problems.append(CrawlProblem(*left, f'检测未完成：与{right[0]}没有共同期号'))
    return problems


def matching_runs_by_issue(
    left_by_issue: dict[str, str],
    right_by_issue: dict[str, str],
) -> list[list[tuple[str, str]]]:
    common_issues = sorted(
        set(left_by_issue) & set(right_by_issue),
        key=lambda value: int(value),
    )
    runs: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    previous_issue: int | None = None

    for issue in common_issues:
        issue_number = int(issue)
        numbers_match = left_by_issue[issue] == right_by_issue[issue]
        is_continuous = previous_issue is not None and issue_number == previous_issue + 1

        if numbers_match and (not current or is_continuous):
            current.append((issue, left_by_issue[issue]))
        else:
            if current:
                runs.append(current)
            current = [(issue, left_by_issue[issue])] if numbers_match else []

        previous_issue = issue_number

    if current:
        runs.append(current)
    return runs


def site_duplicate_matches(records: list[Record]) -> list[SiteDuplicateMatch]:
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
        by_site[key][issue] = record.numbers

    matches: list[SiteDuplicateMatch] = []
    sites = sorted(by_site, key=lambda key: (key[0], key[1]))
    for left_index, left_key in enumerate(sites):
        for right_key in sites[left_index + 1:]:
            runs = matching_runs_by_issue(by_site[left_key], by_site[right_key])
            qualifying_runs = [run for run in runs if len(run) >= 3]
            if not qualifying_runs:
                continue

            best_run = max(
                qualifying_runs,
                key=lambda run: (len(run), int(run[-1][0])),
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

    fuzzy = [
        target.get("url", "")
        for target in targets
        if name and target.get("name") and (name in target["name"] or target["name"] in name)
    ]
    if len(fuzzy) == 1:
        return fuzzy[0]

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
    digits = re.sub(r"\D+", "", str(value))
    return str(int(digits)) if digits else ""


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


def fetch_target_snapshot_source(
    target: dict,
) -> tuple[str, list[SourceDocument], list[str], SourceDocument | None]:
    import crawler

    if target.get("special_parser") in ACQUISITION_ONLY_PARSERS:
        available = crawler.discover_acquisition_only_available_issues(target)
        return target_name(target), [], available, None

    name, documents = crawler.fetch_target_documents(target, [])
    available, selected = crawler.available_issues_for_documents(documents, target)
    required_history = target.get("issue_position_window")
    if (
        not isinstance(required_history, int)
        or isinstance(required_history, bool)
        or required_history <= 0
    ):
        required_history = DEFAULT_RECENT
    if len(available) >= required_history:
        return name, documents, available, selected
    if not (
        target.get("browser_fallback") is True
        or crawler.contract_is_split_across_documents(documents, target)
    ):
        return name, documents, available, selected
    try:
        if target.get("browser_wait_until"):
            browser_documents = crawler.render_page_documents(
                str(target["url"]),
                wait_until=str(target["browser_wait_until"]),
            )
        else:
            browser_documents = crawler.render_page_documents(str(target["url"]))
    except Exception as exc:
        raise RuntimeError(f"浏览器抓取失败：{exc}") from exc
    browser_available, browser_selected = crawler.available_issues_for_documents(
        browser_documents,
        target,
    )
    documents.extend(browser_documents)
    if browser_available == available:
        return name, documents, available, selected
    http_set = set(available)
    browser_set = set(browser_available)
    if http_set < browser_set and [
        issue for issue in browser_available if issue in http_set
    ] == available:
        return name, documents, browser_available, browser_selected
    if browser_set < http_set and [
        issue for issue in available if issue in browser_set
    ] == browser_available:
        return name, documents, available, selected
    raise ValueError(
        "HTTP 与浏览器识别到的历史期数或方向顺序冲突："
        f"HTTP={available or '无'}；浏览器={browser_available or '无'}"
    )


def fetch_target_content(target: dict) -> tuple[str, list]:
    name, documents, _available, _selected = fetch_target_snapshot_source(target)
    return name, documents


def snapshot_for_target(target: dict) -> TargetSnapshot:
    import crawler

    name, documents, available, selected_document = fetch_target_snapshot_source(target)
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


def recent_count_for_snapshot(snapshot: TargetSnapshot, fallback_recent_count: int) -> int:
    target = snapshot.target or {}
    configured = target.get("recent_history_count")
    if isinstance(configured, int) and not isinstance(configured, bool):
        return configured
    return fallback_recent_count


def fetch_target_snapshots(
    workers: int,
) -> tuple[list[TargetSnapshot], list[CrawlProblem], list[RegionProblem], str | None]:
    import crawler

    targets = list(getattr(crawler, "TARGETS", []))
    region_problems = region_config_problems(targets)
    snapshots: list[TargetSnapshot] = []
    problems: list[CrawlProblem] = []
    max_workers = max(1, min(workers, len(targets) or 1))

    print(f"自动探测最新期：读取目标 {len(targets)} 个，并发 {max_workers}")
    for done_count, entry in enumerate(
        iter_completed_batch(targets, snapshot_for_target, max_workers),
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


def records_from_snapshot(snapshot: TargetSnapshot, issues: list[str]) -> tuple[list[Record], CrawlProblem | None]:
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
            )
            for result in accepted
        ], None

    issue_map: dict[str, list[str]] = {}
    selected_documents: dict[str, SourceDocument] = {}
    try:
        for requested_issue in issues:
            issue = issue_key(requested_issue)
            partial, document = crawler.parse_target_documents(
                snapshot.documents,
                target,
                [issue],
            )
            if issue in partial and document is not None:
                issue_map[issue] = partial[issue]
                selected_documents[issue] = document
        if issue_map:
            issue_map = crawler.validate_issue_map(
                target,
                issues,
                issue_map,
                source_documents=selected_documents,
                require_source_document=True,
            )
    except Exception as exc:
        reason = str(exc)
        debug_path = crawler.save_debug_page(
            target,
            issues,
            snapshot.name,
            snapshot.selected_content,
            reason,
        )
        if debug_path:
            reason = f"{reason}；调试页面：{debug_path.name}"
        return [], CrawlProblem(snapshot.name, snapshot.url, reason)

    selected_values = []
    selected_ids = set()
    for document in selected_documents.values():
        if id(document) not in selected_ids:
            selected_ids.add(id(document))
            selected_values.append(document)
    content = selected_values[0].content if len(selected_values) == 1 else snapshot.selected_content
    if issue_map:
        return [
            Record(
                source_file="实时抓取缓存",
                line_no=0,
                numbers=normalize_numbers(",".join(numbers)),
                name=snapshot.name,
                issue=f"{issue}期",
                raw="",
                url=snapshot.url,
            )
            for issue, numbers in issue_map.items()
        ], None

    mismatch = crawler.diagnose_issue_mismatch(
        content,
        issues,
        keywords=target.get("keywords"),
        expected_count=target.get("count"),
        allow_duplicate_numbers=target.get("allow_duplicate_numbers", False),
        region=target_issue_region(target),
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
    """Identify a logical named section, not merely its containing page URL."""
    normalized_name = name.strip()
    normalized_url = url.strip()
    if normalized_name and normalized_url:
        return normalized_name, normalized_url
    return ("", normalized_url) if normalized_url else (normalized_name, "")


def validate_cache_data(
    data: object,
    path: Path,
    expected_recent_count: int | None = None,
    targets: list[dict] | None = None,
) -> list[dict]:
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError(f"缓存文件结构或版本错误：{path}")
    timestamp_key = "updated_at" if "sites" in data else "generated_at"
    if not isinstance(data.get(timestamp_key), str) or not data[timestamp_key].strip():
        raise ValueError(f"缓存文件 {timestamp_key} 无效：{path}")

    recent_key = "periods" if "sites" in data else "recent_count"
    recent_count = data.get(recent_key)
    if isinstance(recent_count, bool) or not isinstance(recent_count, int) or recent_count <= 0:
        raise ValueError(f"缓存文件 {recent_key} 无效：{path}")
    if expected_recent_count is not None and recent_count != expected_recent_count:
        raise ValueError(
            f"缓存文件 {recent_key} 不匹配：期望 {expected_recent_count}，实际 {recent_count}"
        )

    if "sites" in data:
        sites = data.get("sites")
        if not isinstance(sites, list) or not sites:
            raise ValueError(f"缓存文件 sites 缺失或为空：{path}")
        for index, site in enumerate(sites, start=1):
            if not isinstance(site, dict):
                raise ValueError(f"缓存文件第 {index} 个站点格式错误：{path}")
            if (
                not isinstance(site.get("id"), str)
                or not site["id"].strip()
                or not isinstance(site.get("name"), str)
                or not site["name"].strip()
                or not isinstance(site.get("url"), str)
                or not site["url"].strip()
                or not isinstance(site.get("pick"), str)
                or not isinstance(site.get("browser"), bool)
                or not isinstance(site.get("click_first"), bool)
                or not isinstance(site.get("fingerprint"), dict)
            ):
                raise ValueError(f"缓存文件第 {index} 个站点字段无效：{path}")
        base_period = data.get("base_period")
        if isinstance(base_period, bool) or not isinstance(base_period, int) or base_period <= 0:
            raise ValueError(f"缓存文件 base_period 无效：{path}")

    records = records_from_cache_document(data)
    failure_records = failures_from_cache_document(data)
    if not isinstance(records, list):
        raise ValueError(f"缓存文件记录格式错误：{path}")
    if "failures" in data and not isinstance(data.get("failures"), list):
        raise ValueError(f"缓存文件 failures 格式错误：{path}")
    if not isinstance(failure_records, list):
        raise ValueError(f"缓存文件 failures 格式错误：{path}")
    if not records and not failure_records:
        raise ValueError(f"缓存文件记录和失败状态均为空：{path}")

    failure_sites: set[tuple[str, str]] = set()
    failure_keys: set[tuple[tuple[str, str], str]] = set()
    for index, item in enumerate(failure_records, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"缓存文件第 {index} 条失败状态格式错误：{path}")
        name = item.get("name")
        url = item.get("url")
        issue = issue_key(item.get("issue", ""))
        status = item.get("status")
        reason = item.get("reason")
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(url, str)
            or not url.strip()
            or not isinstance(status, str)
            or status != "failed"
            or not isinstance(reason, str)
            or not reason.strip()
            or not issue
            or "numbers" in item
        ):
            raise ValueError(f"缓存文件第 {index} 条失败状态无效：{path}")
        site = cache_site_key(name, url)
        conflict_key = (site, issue)
        if conflict_key in failure_keys:
            raise ValueError(f"缓存文件同站同期失败状态重复：{name} {issue}期")
        failure_keys.add(conflict_key)
        failure_sites.add(site)

    seen_by_site_issue: dict[tuple[tuple[str, str], str], str] = {}
    available_sites: set[tuple[str, str]] = set()
    targets_by_site = {
        cache_site_key(target_name(target), str(target.get("url") or "")): target
        for target in (targets or [])
    }
    for index, item in enumerate(records, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"缓存文件第 {index} 条记录格式错误：{path}")
        name = item.get("name")
        url = item.get("url")
        numbers = item.get("numbers")
        issue = issue_key(item.get("issue", ""))
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(url, str)
            or not isinstance(numbers, str)
            or not normalize_numbers(numbers)
            or not issue
        ):
            raise ValueError(f"缓存文件第 {index} 条记录字段无效：{path}")

        site = cache_site_key(name, url)
        target = targets_by_site.get(site)
        if target is not None:
            tokens = [token for token in re.split(r"[,\.\s]+", numbers.strip()) if token]
            validation_target = target
            exception = (target.get("cache_issue_exceptions") or {}).get(issue)
            if isinstance(exception, dict) and exception.get("approved") is True:
                validation_target = dict(target)
                validation_target["count"] = exception.get("count")
            try:
                validate_issue_map(validation_target, [issue], {issue: tokens})
            except ValueError as exc:
                raise ValueError(f"缓存文件 {name} {issue}期数据无效：{exc}") from exc
        conflict_key = (site, issue)
        if conflict_key in failure_keys:
            raise ValueError(f"缓存文件同站同期同时存在成功号码和失败状态：{name} {issue}期")
        normalized_numbers = normalize_numbers(numbers)
        previous_numbers = seen_by_site_issue.get(conflict_key)
        if previous_numbers is not None and previous_numbers != normalized_numbers:
            raise ValueError(f"缓存文件同站同期冲突：{name} {issue}期")
        seen_by_site_issue[conflict_key] = normalized_numbers
        available_sites.add(site)

    if "sites" in data:
        actual_issues = [
            int(issue_key(item.get("issue", "")))
            for item in records + failure_records
            if issue_key(item.get("issue", ""))
        ]
        expected_base_period = max(actual_issues, default=0)
        if data.get("base_period") != expected_base_period:
            raise ValueError(
                "缓存文件 base_period 与实际期数不一致："
                f"标注 {data.get('base_period')}，实际 {expected_base_period}"
            )
        by_site_periods = defaultdict(list)
        for item in records + failure_records:
            by_site_periods[cache_site_key(item['name'],item['url'])].append(int(issue_key(item['issue'])))
        if any(min(periods) < max(periods)-recent_count+1 for periods in by_site_periods.values()):
            raise ValueError(f"缓存文件包含站点自身近 {recent_count} 期窗口外数据")

    if targets is not None:
        cached_contracts = {
            cache_site_key(str(site.get("name") or ""), str(site.get("url") or "")):
            str(site.get("contract") or "")
            for site in data.get("sites", [])
            if isinstance(site, dict)
        }
        enabled_sites = {
            cache_site_key(target_name(target), str(target.get("url") or ""))
            for target in targets
        }
        present_sites = available_sites | failure_sites
        missing = [
            target_name(target)
            for target in targets
            if cache_site_key(target_name(target), str(target.get("url") or "")) not in present_sites
        ]
        if missing:
            raise ValueError(f"缓存文件缺少启用目标：{', '.join(missing)}")
        unexpected_sites = present_sites - enabled_sites
        unexpected = sorted({
            str(item.get("name") or item.get("url") or "").strip()
            for item in records + failure_records
            if cache_site_key(str(item.get("name") or ""), str(item.get("url") or ""))
            in unexpected_sites
        })
        if unexpected:
            raise ValueError(f"缓存文件包含未启用目标：{', '.join(unexpected)}")
        for target in targets:
            site = cache_site_key(target_name(target), str(target.get("url") or ""))
            cached_contract = cached_contracts.get(site, "")
            expected_contract = target_contract_fingerprint(target)
            if cached_contract != expected_contract:
                raise ValueError(f"缓存文件配置指纹不匹配：{target_name(target)}")
    return records


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
    readiness = duplicate_readiness_problems(records, recent_count, targets)
    if readiness:
        raise ValueError('；'.join(item.reason for item in readiness))

    data = build_cache_document(
        records,
        [],
        recent_count=recent_count,
        targets=targets,
        updated_at=datetime.now().isoformat(timespec="seconds"),
    )
    validate_cache_data(data, path, expected_recent_count=recent_count, targets=targets)

    atomic_write_json(path, data, trailing_newline=False)


def load_cache_state(
    path: Path,
    expected_recent_count: int | None = None,
    targets: list[dict] | None = None,
) -> tuple[list[Record], list[CrawlProblem]]:
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
            )
        )
    problems = [
        CrawlProblem(
            str(item.get("name") or ""),
            str(item.get("url") or ""),
            f"{issue_key(item.get('issue', ''))}期缓存失败：{item.get('reason', '')}",
        )
        for item in failures_from_cache_document(data)
    ]
    return loaded, problems


def load_records_cache(
    path: Path,
    expected_recent_count: int | None = None,
    targets: list[dict] | None = None,
) -> list[Record]:
    records, _problems = load_cache_state(path, expected_recent_count, targets)
    return records


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
                    )
                )
            print(f"[{done_count}/{len(targets)}] 成功：{url} ({len(found)} 条)")
        else:
            reason = failure.reason if failure else "没有返回数据"
            problems.append(CrawlProblem(target_name, url, reason))
            print(f"[{done_count}/{len(targets)}] 无数据：{url}")

    return records, problems


def write_report(
    output: Path,
    files: list[Path],
    records: list[Record],
    duplicates: dict[tuple[str, str], list[Record]],
    cross_duplicates: dict[str, list[Record]],
    bad_lines: list[str],
    problems: list[CrawlProblem],
    issues: list[str] | None = None,
) -> None:
    lines: list[str] = []
    lines.append("重复检测结果")
    lines.append("")
    lines.append("判断规则：原始号码顺序完全一致 = 重复；不自动排序。")
    if issues:
        lines.append(f"检测期数：{', '.join(issue + '期' for issue in issues)}")
        lines.append("数据来源：实时抓取 crawler.py 里的全部目标")
    else:
        lines.append(f"读取文件：{', '.join(path.name for path in files) if files else '无'}")
    lines.append(f"读取记录：{len(records)} 条")
    lines.append(f"同一期重复组数：{len(duplicates)} 组")
    lines.append(f"跨期重复组数：{len(cross_duplicates)} 组")
    if problems:
        lines.append(f"抓取/识别异常：{len(problems)} 个")
    lines.append("")

    lines.append("一、同一期重复")
    lines.append("")
    if duplicates:
        for index, ((issue, numbers), items) in enumerate(
            sorted(duplicates.items(), key=lambda item: (int(item[0][0].replace("期", "")), item[0][1])),
            start=1,
        ):
            lines.append(f"{index}. {issue} 重复号码：{numbers}")
            for item in items:
                location = f"{item.source_file}:{item.line_no}" if item.line_no else item.source_file
                lines.append(f"   - {item.name} ({location})")
                lines.append(f"     网址：{item.url or '未匹配到'}")
            lines.append("")
    else:
        lines.append("没有发现重复。")
        lines.append("")

    lines.append("二、跨期重复")
    lines.append("")
    if cross_duplicates:
        for index, (numbers, items) in enumerate(
            sorted(
                cross_duplicates.items(),
                key=lambda item: (
                    min(int(record.issue.replace("期", "")) for record in item[1]),
                    item[0],
                ),
            ),
            start=1,
        ):
            issue_text = ", ".join(sorted({item.issue for item in items}, key=lambda value: int(value.replace("期", ""))))
            lines.append(f"{index}. 跨期重复号码：{numbers}")
            lines.append(f"   出现期数：{issue_text}")
            for item in sorted(items, key=lambda value: (int(value.issue.replace("期", "")), value.name)):
                location = f"{item.source_file}:{item.line_no}" if item.line_no else item.source_file
                lines.append(f"   - {item.issue} {item.name} ({location})")
                lines.append(f"     网址：{item.url or '未匹配到'}")
            lines.append("")
    else:
        lines.append("没有发现跨期重复。")
        lines.append("")

    if problems:
        lines.append("三、抓取/识别异常")
        lines.append("")
        for item in problems:
            lines.append(f"- {item.name}")
            lines.append(f"  网址：{item.url}")
            lines.append(f"  原因：{item.reason}")
        lines.append("")

    if bad_lines:
        lines.append("四、未识别行")
        lines.append("")
        for item in bad_lines:
           lines.append(f"   - {item}")

    atomic_write_text(output, "\n".join(lines))


def format_issue(value: str) -> str:
    key = issue_key(value)
    return f"{key}期" if key else str(value)


def site_match_status_label(status: str) -> str:
    if status == "reject":
        return "重复网站，直接拒收"
    if status == "suspect":
        return "疑似重复，交给人工审核"
    return "不处理"


def write_report_v2(
    output: Path,
    files: list[Path],
    records: list[Record],
    duplicates: dict[tuple[str, str], list[Record]],
    cross_duplicates: dict[str, list[Record]],
    site_matches: list[SiteDuplicateMatch],
    bad_lines: list[str],
    problems: list[CrawlProblem],
    region_problems: list[RegionProblem] | None = None,
    issues: list[str] | None = None,
    latest_issue: str | None = None,
) -> None:
    region_problems = region_problems or []
    incomplete = bool(problems or region_problems or bad_lines)
    lines: list[str] = []
    lines.append("重复检测结果")
    lines.append("")
    if incomplete:
        lines.append("结论：检测未完成；存在未验证站点或未识别输入，不能据此判断全部站点无重复。")
        lines.append("")
    lines.append("判断规则：同一期内，原始号码整串顺序完全一致 = 重复；不自动排序；不检测部分相同。")
    if issues:
        if latest_issue:
            lines.append(f"自动识别全站参考最大期：{latest_issue}期")
        lines.append(f"检测期数：{', '.join(format_issue(issue) for issue in issues)}")
        lines.append("数据来源：先从所有站点实际抓取数据；每个站点按自己的 region 判断最新期，再取该站点近10期。")
    else:
        lines.append(f"读取文件：{', '.join(path.name for path in files) if files else '无'}")
    lines.append(f"读取记录：{len(records)} 条")
    lines.append(f"同一期重复组数：{len(duplicates)} 组")
    lines.append(f"跨期重复组数：{len(cross_duplicates)} 组")
    lines.append(f"新增网站连续重复风险：{len(site_matches)} 组")
    if region_problems:
        lines.append(f"region 配置异常：{len(region_problems)} 个")
    if problems:
        lines.append(f"抓取/识别异常：{len(problems)} 个")
    lines.append("")

    lines.append("新增网站连续重复判断")
    lines.append("")
    lines.append("规则：按双方共同拥有的具体期号对齐比较；期号连续且号码整串一致才累计。连续1-2期不处理，连续3-5期疑似重复，连续6期或以上直接拒收。")
    lines.append("")
    if site_matches:
        for index, item in enumerate(site_matches, start=1):
            lines.append(f"{index}. {site_match_status_label(item.status)}：连续 {item.max_run_length} 期一致")
            lines.append(f"   网站A：{item.name_a}")
            lines.append(f"   网址A：{item.url_a or '未匹配到'}")
            lines.append(f"   网站B：{item.name_b}")
            lines.append(f"   网址B：{item.url_b or '未匹配到'}")
            lines.append(f"   连续期数：{', '.join(format_issue(issue) for issue in item.issues)}")
            for issue, numbers in item.numbers_by_issue:
                lines.append(f"   - {format_issue(issue)}：{numbers}")
            lines.append("")
    else:
        lines.append(
            "检测未完成；已完成记录中未发现连续3期以上重复，不能据此判断全部站点无重复。"
            if incomplete
            else "没有发现达到连续3期以上的同网站重复风险。"
        )
        lines.append("")

    lines.append("一、同一期重复")
    lines.append("")
    if duplicates:
        for index, ((issue, numbers), items) in enumerate(
            sorted(duplicates.items(), key=lambda item: (int(issue_key(item[0][0]) or 0), item[0][1])),
            start=1,
        ):
            lines.append(f"{index}. {format_issue(issue)} 重复号码：{numbers}")
            for item in items:
                location = f"{item.source_file}:{item.line_no}" if item.line_no else item.source_file
                lines.append(f"   - {item.name} ({location})")
                lines.append(f"     网址：{item.url or '未匹配到'}")
            lines.append("")
    else:
        lines.append(
            "检测未完成；已完成记录中未发现同一期整串重复。"
            if incomplete
            else "没有发现同一期整串重复。"
        )
        lines.append("")

    lines.append("二、跨期重复")
    lines.append("")
    if cross_duplicates:
        for index, (numbers, items) in enumerate(
            sorted(
                cross_duplicates.items(),
                key=lambda item: (
                    min(int(issue_key(record.issue) or 0) for record in item[1]),
                    item[0],
                ),
            ),
            start=1,
        ):
            issue_text = ", ".join(
                sorted({format_issue(item.issue) for item in items}, key=lambda value: int(issue_key(value) or 0))
            )
            lines.append(f"{index}. 跨期重复号码：{numbers}")
            lines.append(f"   出现期数：{issue_text}")
            for item in sorted(items, key=lambda value: (int(issue_key(value.issue) or 0), value.name)):
                location = f"{item.source_file}:{item.line_no}" if item.line_no else item.source_file
                lines.append(f"   - {format_issue(item.issue)} {item.name} ({location})")
                lines.append(f"     网址：{item.url or '未匹配到'}")
            lines.append("")
    else:
        lines.append(
            "检测未完成；已完成记录中未发现跨期整串重复。"
            if incomplete
            else "没有发现跨期整串重复。"
        )
        lines.append("")

    section_no = 3
    if region_problems:
        lines.append(f"{section_no}、region 配置异常")
        lines.append("")
        for item in region_problems:
            lines.append(f"- {item.name}")
            lines.append(f"  网址：{item.url}")
            lines.append(f"  region：{item.region or '未配置'}")
            lines.append(f"  原因：{item.reason}")
        lines.append("")
        section_no += 1

    if problems:
        lines.append(f"{section_no}、抓取/识别异常")
        lines.append("")
        lines.append("说明：这些站点本次没有参与完整重复检测，需要人工按目录/调试文件审核。")
        for item in problems:
            lines.append(f"- {item.name}")
            lines.append(f"  网址：{item.url or '无'}")
            lines.append(f"  原因：{item.reason}")
        lines.append("")
        section_no += 1

    if bad_lines:
        lines.append(f"{section_no}、未识别行")
        lines.append("")
        for item in bad_lines:
            lines.append(f"   - {item}")

    atomic_write_text(output, "\n".join(lines))


def _main_unlocked() -> int:
    parser = argparse.ArgumentParser(description="多期检测原始号码重复，不自动排序")
    parser.add_argument(
        "files",
        nargs="*",
        help="可指定文件；指定文件时只读文件，不实时抓取",
    )
    parser.add_argument("--issues", help="指定检测期数，例如：119,120,121")
    parser.add_argument("--latest", help="指定最新期；程序按 --recent 自动往前取近几期，例如 158 会取 149-158")
    parser.add_argument("--recent", type=int, default=DEFAULT_RECENT, help="不指定 --issues 时，默认从 crawler.py 默认期数往前取几期")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="实时抓取并发数，默认 8 个网站")
    parser.add_argument("--from-files", action="store_true", help="不实时抓取，读取 *期成功.txt 和 results.txt")
    parser.add_argument("--from-cache", action="store_true", help="不实时抓全站，读取最近10期缓存 JSON")
    parser.add_argument("--write-cache", action="store_true", help="实时抓取后覆盖写入最近10期缓存 JSON")
    parser.add_argument("--cache", default=DEFAULT_CACHE, help="最近10期缓存 JSON 文件名")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="输出报告文件名")
    args = parser.parse_args()
    if args.recent < 1:
        parser.error('--recent 必须为正整数')

    files: list[Path] = []
    bad_lines: list[str] = []
    problems: list[CrawlProblem] = []
    region_problems: list[RegionProblem] = []
    issues: list[str] | None = None
    latest_issue: str | None = None

    if args.from_cache:
        import crawler

        cache_path = Path(args.cache)
        targets = list(getattr(crawler, "TARGETS", []))
        records, problems = load_cache_state(cache_path, args.recent, targets)
        issues = sorted({issue_key(record.issue) for record in records if issue_key(record.issue)}, key=lambda value: int(value))
        latest_issue = str(max(int(issue) for issue in issues)) if issues else None
    elif args.files or args.from_files:
        files = [Path(item) for item in args.files] if args.files else find_input_files(["*期成功.txt", "*期-杀数字-成功.txt"])
        records, bad_lines = read_records(files)
        add_urls(records)
        if args.issues:
            issues = parse_issues(args.issues)
            records = filter_records_by_issues(records, issues)
            latest_issue = str(max(int(issue) for issue in issues)) if issues else None
            problems.extend(multi_issue_completeness_problems(records, issues))
    else:
        if args.issues and args.latest:
            parser.error("--issues 和 --latest 只能二选一")
        if args.issues or args.latest:
            import crawler

            if args.latest:
                latest_issue = issue_key(args.latest)
                issues = recent_issues_from_latest(args.latest, args.recent)
            else:
                issues = parse_issues(args.issues)
                latest_issue = str(max(int(issue) for issue in issues)) if issues else None
            region_problems = region_config_problems(list(getattr(crawler, "TARGETS", [])))
            records, problems = records_from_crawler(issues, args.workers)
            conflicts = record_conflict_problems(records)
            problems.extend(conflicts)
            problems.extend(multi_issue_completeness_problems(records, issues))
            if args.write_cache and not problems:
                write_records_cache(
                    Path(args.cache),
                    records,
                    issues,
                    args.recent,
                    problems=problems,
                    region_problems=region_problems,
                    targets=list(getattr(crawler, "TARGETS", [])),
                )
        else:
            snapshots, discovery_problems, region_problems, latest_issue = fetch_target_snapshots(args.workers)
            problems.extend(discovery_problems)
            if latest_issue:
                records, crawl_problems, issues = records_from_recent_snapshots(snapshots, args.recent)
                problems.extend(crawl_problems)
                conflicts = record_conflict_problems(records)
                problems.extend(conflicts)
                if args.issues:
                    problems.extend(multi_issue_completeness_problems(records, issues))
                if args.write_cache and not problems:
                    import crawler

                    write_records_cache(
                        Path(args.cache),
                        records,
                        issues,
                        args.recent,
                        problems=problems,
                        region_problems=region_problems,
                        targets=list(getattr(crawler, "TARGETS", [])),
                    )
            else:
                issues = []
                records = []

    conflicts = record_conflict_problems(records)
    for conflict in conflicts:
        if not any(
            item.name == conflict.name
            and item.url == conflict.url
            and item.reason == conflict.reason
            for item in problems
        ):
            problems.append(conflict)

    if args.issues:
        for problem in multi_issue_completeness_problems(records, issues):
            if not any(
                item.name == problem.name
                and item.url == problem.url
                and item.reason == problem.reason
                for item in problems
            ):
                problems.append(problem)

    import crawler
    readiness_targets = list(crawler.TARGETS)
    if args.files or args.from_files:
        selected_sites = {site_key(record) for record in records}
        readiness_targets = [t for t in readiness_targets if (target_name(t),str(t.get('url') or '')) in selected_sites]
    problems.extend(duplicate_readiness_problems(records, args.recent, readiness_targets))
    duplicates = duplicate_groups(records)
    cross_duplicates = cross_issue_groups(records)
    site_matches = [] if conflicts else site_duplicate_matches(records)
    write_report_v2(
        Path(args.output),
        files,
        records,
        duplicates,
        cross_duplicates,
        site_matches,
        bad_lines,
        problems,
        region_problems,
        issues,
        latest_issue,
    )

    print(f"完成：读取 {len(records)} 条，连续重复风险 {len(site_matches)} 组，同期重复 {len(duplicates)} 组，跨期重复 {len(cross_duplicates)} 组")
    print(f"报告：{args.output}")
    if problems or region_problems or bad_lines:
        return 4
    return max((6 if match.status == 'reject' else 5 if match.status == 'suspect' else 0 for match in site_matches), default=0)


def main() -> int:
    try:
        with exclusive_run_lock(Path(__file__).resolve().parent / ".crawler-and-duplicates.lock"):
            return _main_unlocked()
    except (RuntimeError, ValueError, OSError) as exc:
        print(str(exc))
        return 2
    finally:
        import crawler

        crawler.close_browser_pool()


if __name__ == "__main__":
    raise SystemExit(main())
