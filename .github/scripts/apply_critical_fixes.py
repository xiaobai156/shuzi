from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    content = read(path)
    if content.count(old) != 1:
        raise RuntimeError(
            f"{path}: expected exactly one literal match, got {content.count(old)}\n{old[:200]}"
        )
    write(path, content.replace(old, new, 1))


def replace_all(path: str, old: str, new: str, expected_min: int = 1) -> None:
    content = read(path)
    count = content.count(old)
    if count < expected_min:
        raise RuntimeError(f"{path}: expected at least {expected_min} matches, got {count}: {old[:200]}")
    write(path, content.replace(old, new))


def regex_once(path: str, pattern: str, replacement: str) -> None:
    content = read(path)
    updated, count = re.subn(pattern, replacement, content, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{path}: regex expected one match, got {count}: {pattern[:200]}")
    write(path, updated)


# ---------------------------------------------------------------------------
# 1. Common parsing: configured windows, whole-group rejection, strict bounds.
# ---------------------------------------------------------------------------
replace_once(
    "kill_numbers/parsing/common.py",
    '''# Direction is a business rule, not a per-site tuning knob.  Every top/bottom
# parser must use the same three-candidate boundary.
CANDIDATE_REGION_WINDOW = 3
''',
    '''# Default direction boundary. A target-level issue_position_window overrides it.
CANDIDATE_REGION_WINDOW = 5


def resolve_candidate_window(value: object) -> int:
    if value is None:
        return CANDIDATE_REGION_WINDOW
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"issue_position_window 必须是正整数，实际为：{value!r}")
    return value
''',
)

replace_once(
    "kill_numbers/parsing/common.py",
    '''            nums = re.findall(r"\\d{2}", match.group(0))
            nums = [num for num in nums if valid_number(num)]
            if len(nums) >= 3:
                found.append(nums)
''',
    '''            nums = re.findall(r"\\d{2}", match.group(0))
            # Never repair an invalid group by deleting 00/>49 tokens.  One
            # invalid token invalidates the whole source group.
            if len(nums) >= 3 and all(valid_number(num) for num in nums):
                found.append(nums)
''',
)

replace_once(
    "kill_numbers/parsing/common.py",
    '''    end_index = len(text)
    for stop_anchor in as_list(stop_anchors):
        candidate = find_anchor_index(text, stop_anchor, start=start_index + 1)
        if candidate >= 0:
            end_index = min(end_index, candidate)

    return text[start_index:end_index], start_index
''',
    '''    stop_anchor_list = as_list(stop_anchors)
    if stop_anchor_list:
        candidates = [
            find_anchor_index(text, stop_anchor, start=start_index + 1)
            for stop_anchor in stop_anchor_list
        ]
        candidates = [candidate for candidate in candidates if candidate >= 0]
        if not candidates:
            raise ValueError(f"找到正文锚点，但没有找到结束锚点：{stop_anchor_list}")
        end_index = min(candidates)
    else:
        end_index = len(text)

    return text[start_index:end_index], start_index
''',
)

regex_once(
    "kill_numbers/parsing/common.py",
    r'''def filter_candidates_by_region\(.*?\n    return ordered\[-CANDIDATE_REGION_WINDOW:\]\n''',
    '''def filter_candidates_by_region(
    candidates: list[tuple[list[str], str, int]],
    region: str | None,
    text_length: int | None = None,
    strict_window: bool = False,
    require_region: bool = False,
    window_size: int | None = None,
):
    _ = text_length, strict_window
    region = normalize_region(region)
    if require_region and not region:
        return []
    if not region:
        return candidates

    window = resolve_candidate_window(window_size)
    ordered = sorted(candidates, key=lambda item: item[2])
    if region == "top":
        return ordered[:window]
    return ordered[-window:]
''',
)

regex_once(
    "kill_numbers/parsing/common.py",
    r'''def issue_position_window_starts\(.*?\n(?=def extract_issue_numbers\()''',
    '''def issue_position_window_starts(
    text: str,
    keywords: list[str] | None,
    expected_count: int | None,
    region: str | None,
    issue_position_window: int | None,
    allow_duplicate_numbers: bool = False,
) -> set[int] | None:
    region = normalize_region(region)
    if not region:
        return None

    window = resolve_candidate_window(issue_position_window)
    keyword_list = [normalize_keyword(keyword) for keyword in (keywords or []) if keyword]
    candidates: list[tuple[list[str], str, int]] = []
    for match in iter_all_issue_segment_matches(text):
        segment = match.group(0)
        compact_segment = normalize_keyword(segment)
        if keyword_list and not any(keyword in compact_segment for keyword in keyword_list):
            continue
        groups = [
            group
            for group in find_number_groups(segment)
            if (not expected_count or len(group) == expected_count)
            and (allow_duplicate_numbers or not has_duplicate_numbers(group))
        ]
        if groups:
            # A source row occupies one position regardless of how many number
            # groups it contains.  All groups are evaluated later for conflict.
            candidates.append((groups[0], segment, match.start()))
            if region == "top" and len(candidates) >= window:
                break

    ordered = sorted(candidates, key=lambda item: item[2])
    selected = ordered[:window] if region == "top" else ordered[-window:]
    return {start for _group, _segment, start in selected}

''',
)

regex_once(
    "kill_numbers/parsing/common.py",
    r'''def extract_issue_numbers\(.*\Z''',
    '''def extract_issue_numbers(
    text: str,
    issues: list[str],
    keywords: list[str] | None = None,
    expected_count: int | None = None,
    position: str = "first",
    strict_ambiguous: bool = False,
    allow_duplicate_numbers: bool = False,
    anchor=None,
    stop_anchor=None,
    region: str | None = None,
    issue_position_window: int | None = None,
) -> dict[str, list[str]]:
    found = {}
    full_text = html_to_text(text)
    text, scope_offset = scope_text_by_anchor_with_offset(full_text, anchor, stop_anchor)
    keyword_list = [normalize_keyword(keyword) for keyword in (keywords or []) if keyword]
    allowed_window_starts = issue_position_window_starts(
        text,
        keywords,
        expected_count,
        region,
        issue_position_window,
        allow_duplicate_numbers=allow_duplicate_numbers,
    )
    for issue in issues:
        candidates: list[tuple[list[str], str, int]] = []
        for match in issue_segment_matches(text, issue):
            if allowed_window_starts is not None and match.start() not in allowed_window_starts:
                continue
            segment = match.group(0)
            compact_segment = normalize_keyword(segment)
            if keyword_list and not any(keyword in compact_segment for keyword in keyword_list):
                continue
            for group in find_number_groups(segment):
                if expected_count and len(group) != expected_count:
                    continue
                if not allow_duplicate_numbers and has_duplicate_numbers(group):
                    continue
                candidates.append((group, segment, scope_offset + match.start()))

        if not candidates:
            continue

        normalized_region = normalize_region(region)
        # A configured region has already been applied to valid source rows by
        # issue_position_window_starts.  Applying a second window to individual
        # number groups could hide a conflict in the last selected row.
        if allowed_window_starts is None and normalized_region:
            candidates = filter_candidates_by_region(
                candidates,
                normalized_region,
                window_size=issue_position_window,
            )

        candidate_position = position
        if normalized_region:
            candidate_position = "last" if normalized_region == "bottom" else "first"
        selected = select_candidate(
            [(group, segment) for group, segment, _offset in candidates],
            position=candidate_position,
            strict_ambiguous=strict_ambiguous,
            issue=issue,
            allow_duplicate_numbers=allow_duplicate_numbers,
        )
        if selected:
            found[normalize_issue(issue)] = selected
    return found
''',
)

# Diagnostics must use exactly the same window and validity rules.
replace_once(
    "kill_numbers/parsing/diagnostics.py",
    '''    needs_strict_region_window,
    normalize_region,
    scope_text_by_anchor,
)''',
    '''    needs_strict_region_window,
    normalize_region,
    resolve_candidate_window,
    scope_text_by_anchor,
)''',
)
replace_all(
    "kill_numbers/parsing/diagnostics.py",
    '''        region,
        issue_position_window,
    )''',
    '''        region,
        issue_position_window,
        allow_duplicate_numbers=allow_duplicate_numbers,
    )''',
    expected_min=2,
)
replace_once(
    "kill_numbers/parsing/diagnostics.py",
    '''    normalized_region = normalize_region(region)
    allowed_window_starts = issue_position_window_starts(''',
    '''    normalized_region = normalize_region(region)
    window = resolve_candidate_window(issue_position_window)
    allowed_window_starts = issue_position_window_starts(''',
)
replace_all(
    "kill_numbers/parsing/diagnostics.py",
    "{CANDIDATE_REGION_WINDOW}",
    "{window}",
    expected_min=2,
)
replace_once(
    "kill_numbers/parsing/diagnostics.py",
    "    if issue_position_window:\n",
    "    if normalize_region(region):\n",
)

# ---------------------------------------------------------------------------
# 2. Evidence: no anchored-to-whole-document fallback; retain scope metadata.
# ---------------------------------------------------------------------------
replace_once(
    "kill_numbers/domain/models.py",
    '''    candidate_start: int = -1
    metadata: dict[str, Any] = field(default_factory=dict)
''',
    '''    candidate_start: int = -1
    region: str = ""
    window_size: int = 0
    row_rank: int = -1
    parser_id: str = ""
    scope_kind: str = ""
    source_identity: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
''',
)

replace_once(
    "kill_numbers/validation/result_validator.py",
    '''    issue_segment_matches,
    scope_text_by_anchor_with_offset,
    valid_number,
)''',
    '''    issue_position_window_starts,
    issue_segment_matches,
    normalize_region,
    resolve_candidate_window,
    scope_text_by_anchor_with_offset,
    target_keywords,
    valid_number,
)''',
)
replace_once(
    "kill_numbers/validation/result_validator.py",
    '''from kill_numbers.text_utils import as_list, html_to_text, normalize_issue, unique_keep_order''',
    '''from kill_numbers.text_utils import (
    as_list,
    html_to_text,
    normalize_issue,
    normalize_keyword,
    unique_keep_order,
)''',
)

regex_once(
    "kill_numbers/validation/result_validator.py",
    r'''def _candidate_location\(.*?\n(?=def _normalize_numbers\()''',
    '''def _candidate_location(
    target: Mapping,
    issue: str,
    numbers: list[str],
    document: SourceDocument,
) -> tuple[str, int, int, int, str, int, int, str, str]:
    text = html_to_text(document.content)
    if target.get("special_parser") == "identity_article_bottom_10":
        text = normalize_identity_article_current_placeholder(text, target)

    anchors = unique_keep_order(
        [
            str(value).strip()
            for value in (
                *as_list(target.get("anchor")),
                *as_list(target.get("source_anchor")),
                target.get("article_identity"),
            )
            if str(value).strip()
        ]
    )
    region = normalize_region(str(target.get("region") or ""))
    window_size = resolve_candidate_window(target.get("issue_position_window"))
    parser_id = str(target.get("special_parser") or "generic")

    def evidence_groups(segment: str) -> list[list[str]]:
        groups = find_number_groups(segment)
        if groups:
            return groups
        expected_count = target.get("count")
        if not isinstance(expected_count, int) or expected_count <= 0:
            return []
        pattern = re.compile(
            rf"(?<!\\d)\\d{{1,2}}(?:[\\s.,，。、;；|/\\\\]+\\d{{1,2}}){{{expected_count - 1}}}"
        )
        found: list[list[str]] = []
        for match in pattern.finditer(segment):
            raw = re.findall(r"\\d{1,2}", match.group(0))
            normalized = [f"{int(number):02d}" for number in raw]
            if len(normalized) == expected_count and all(valid_number(number) for number in normalized):
                found.append(normalized)
        return found

    scopes: list[tuple[str, str, str, int]] = []
    for anchor in anchors:
        try:
            section, section_start = scope_text_by_anchor_with_offset(
                text,
                anchor,
                target.get("stop_anchor"),
            )
        except ValueError:
            continue
        scopes.append(("text_anchor", anchor, section, section_start))

    identity_matches = bool(
        document.identity
        and anchors
        and any(
            normalize_keyword(document.identity) == normalize_keyword(anchor)
            for anchor in anchors
        )
    )
    if identity_matches:
        scopes.append(
            (
                "source_identity",
                f"source_identity:{document.identity}",
                text,
                0,
            )
        )
    if not anchors:
        scopes.append(("unscoped", "", text, 0))
    if not scopes:
        raise ValueError(f"{issue}期没有找到配置的来源锚点或来源身份")

    for scope_kind, anchor_label, section, section_start in scopes:
        allowed_starts = issue_position_window_starts(
            section,
            target_keywords(dict(target)),
            target.get("count"),
            region,
            target.get("issue_position_window"),
            allow_duplicate_numbers=bool(target.get("allow_duplicate_numbers", False)),
        )
        locations: list[tuple[int, int]] = []
        for match in issue_segment_matches(section, issue):
            if allowed_starts is not None and match.start() not in allowed_starts:
                continue
            for group in evidence_groups(match.group(0)):
                normalized = [f"{int(number):02d}" for number in group]
                if normalized == numbers:
                    locations.append((section_start + match.start(), match.start()))
        if not locations:
            continue

        candidate_start, relative_start = (
            locations[-1] if region == "bottom" else locations[0]
        )
        if allowed_starts is None:
            row_rank = 0
        else:
            ordered_starts = sorted(allowed_starts)
            try:
                index = ordered_starts.index(relative_start)
            except ValueError as exc:
                raise ValueError(f"{issue}期来源证据不在配置位置窗口") from exc
            row_rank = len(ordered_starts) - index - 1 if region == "bottom" else index

        return (
            anchor_label,
            section_start,
            section_start + len(section),
            candidate_start,
            region,
            window_size,
            row_rank,
            scope_kind,
            document.identity,
        )

    raise ValueError(f"{issue}期候选缺少同块栏目/位置证据")

''',
)

replace_once(
    "kill_numbers/validation/result_validator.py",
    '''    anchor, section_start, section_end, candidate_start = _candidate_location(
        target,
        normalized_issue,
        normalized_numbers,
        document,
    )
''',
    '''    (
        anchor,
        section_start,
        section_end,
        candidate_start,
        region,
        window_size,
        row_rank,
        scope_kind,
        source_identity,
    ) = _candidate_location(
        target,
        normalized_issue,
        normalized_numbers,
        document,
    )
''',
)
replace_once(
    "kill_numbers/validation/result_validator.py",
    '''        candidate_start=candidate_start,
    )
''',
    '''        candidate_start=candidate_start,
        region=region,
        window_size=window_size,
        row_rank=row_rank,
        parser_id=str(target.get("special_parser") or "generic"),
        scope_kind=scope_kind,
        source_identity=source_identity,
    )
''',
)

regex_once(
    "kill_numbers/validation/result_validator.py",
    r'''def _validate_candidate_evidence\(.*?\n(?=def validate_issue_map\()''',
    '''def _validate_candidate_evidence(
    target: Mapping,
    issue: str,
    numbers: list[str],
    evidence: CandidateEvidence | None,
) -> list[str]:
    if evidence is None:
        return [f"{issue}期来源证据缺失"]

    errors = []
    try:
        evidence_issue = normalize_issue(evidence.issue)
    except (TypeError, ValueError):
        evidence_issue = ""
    if evidence_issue != issue:
        errors.append(f"{issue}期来源证据期数不匹配")
    if list(evidence.numbers) != numbers:
        errors.append(f"{issue}期来源证据号码不匹配")
    if not evidence.source_kind or not evidence.source_url or not evidence.document_fingerprint:
        errors.append(f"{issue}期来源证据不完整")
    if (
        evidence.section_start < 0
        or evidence.section_end <= evidence.section_start
        or evidence.candidate_start < evidence.section_start
        or evidence.candidate_start >= evidence.section_end
    ):
        errors.append(f"{issue}期来源证据缺少同块栏目/偏移")

    expected_region = normalize_region(str(target.get("region") or ""))
    expected_window = resolve_candidate_window(target.get("issue_position_window"))
    expected_parser = str(target.get("special_parser") or "generic")
    if evidence.region != expected_region:
        errors.append(f"{issue}期来源证据方向不匹配")
    if evidence.window_size != expected_window:
        errors.append(f"{issue}期来源证据窗口不匹配")
    if evidence.row_rank < 0 or evidence.row_rank >= expected_window:
        errors.append(f"{issue}期来源证据超出配置位置窗口")
    if evidence.parser_id != expected_parser:
        errors.append(f"{issue}期来源证据解析器不匹配")

    configured_scopes = unique_keep_order(
        [
            str(value).strip()
            for value in (
                *as_list(target.get("anchor")),
                *as_list(target.get("source_anchor")),
                target.get("article_identity"),
            )
            if str(value).strip()
        ]
    )
    if configured_scopes:
        if evidence.scope_kind == "text_anchor":
            if not any(
                normalize_keyword(evidence.anchor) == normalize_keyword(value)
                for value in configured_scopes
            ):
                errors.append(f"{issue}期来源证据锚点不匹配")
        elif evidence.scope_kind == "source_identity":
            if not any(
                normalize_keyword(evidence.source_identity) == normalize_keyword(value)
                for value in configured_scopes
            ):
                errors.append(f"{issue}期来源身份不匹配")
        else:
            errors.append(f"{issue}期来源证据绕过了配置锚点")

    source_pattern = str(target.get("source_url_pattern") or "").strip()
    if source_pattern and not re.search(source_pattern, evidence.source_url, re.I):
        errors.append(f"{issue}期来源证据不符合专属 URL 契约")
    return errors

''',
)
replace_once(
    "kill_numbers/validation/result_validator.py",
    '''                    and list(result.numbers) == numbers
''',
    '''                    and _normalize_numbers(result.numbers, issue=issue)[0] == numbers
''',
)

# ---------------------------------------------------------------------------
# 3. Cache: per-site retention, success/failure exclusivity, cycle timeline.
# ---------------------------------------------------------------------------
write(
    "kill_numbers/infrastructure/cache_repository.py",
    '''import json
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
        cycle = _infer_next_cycle(last[0] if last else None, last[1] if last else 0, value["issue"])

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
            "sequence": run_sequence,
            "cycle": cycle,
        }
        latest_by_site[site] = (value["issue"], cycle, run_sequence)

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

    combined_records = [
        record
        for key, record in record_payload.items()
        if key in retained_payload_keys
    ]
    combined_failures = [
        record
        for key, record in failure_payload.items()
        if key in retained_payload_keys
    ]
    combined_records.sort(key=lambda record: (record["url"].lower(), record["name"], int(record["issue"])))
    combined_failures.sort(key=lambda record: (record["url"].lower(), record["name"], int(record["issue"])))

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
''',
)

# ---------------------------------------------------------------------------
# 4. Crawler orchestration: configured document window and single-period CLI.
# ---------------------------------------------------------------------------
replace_once(
    "crawler.py",
    '''    normalize_region,
    scope_text_by_anchor,
''',
    '''    normalize_region,
    resolve_candidate_window,
    scope_text_by_anchor,
''',
)
replace_once(
    "crawler.py",
    '''RESULTS_DIR = Path(r"C:\\Users\\Administrator\\Desktop\\每天工具\\爬虫合集\\大围杀号生肖数据统一归纳")''',
    '''RESULTS_DIR = Path(
    os.environ.get(
        "SHUZI_RESULTS_DIR",
        r"C:\\Users\\Administrator\\Desktop\\每天工具\\爬虫合集\\大围杀号生肖数据统一归纳",
    )
).expanduser()''',
)
replace_once(
    "crawler.py",
    "import argparse\n",
    "import argparse\nimport os\n",
)
replace_once(
    "crawler.py",
    '''    region = normalize_region(target.get("region"))
    selected_ids: set[int] = set()
''',
    '''    region = normalize_region(target.get("region"))
    window = resolve_candidate_window(target.get("issue_position_window"))
    selected_ids: set[int] = set()
''',
)
replace_once(
    "crawler.py",
    '''            ordered[:CANDIDATE_REGION_WINDOW]
            if region == "top"
            else ordered[-CANDIDATE_REGION_WINDOW:]
''',
    '''            ordered[:window]
            if region == "top"
            else ordered[-window:]
''',
)
replace_once(
    "crawler.py",
    '''    if not issues:
        print("请指定期数，例如：python crawler.py --issues 119")
        return 2

    # Risk checks belong''',
    '''    if not issues:
        print("请指定期数，例如：python crawler.py --issues 119")
        return 2
    if len(issues) != 1:
        print("正式 crawler.py 每次只允许一个期数；多个期数请使用多期入口逐期执行。")
        return 2

    print(f"正式输出目录：{RESULTS_DIR}")

    # Risk checks belong''',
)

# Config value types that previously survived bool/string coercion.
replace_once(
    "crawler.py",
    '''        keywords = item.get("keywords")
        if keywords is not None and (
''',
    '''        issue_window = item.get("issue_position_window")
        if issue_window is not None and (
            isinstance(issue_window, bool)
            or not isinstance(issue_window, int)
            or issue_window <= 0
        ):
            raise ValueError(
                f"targets.json 第 {index} 条 issue_position_window 必须是正整数"
            )
        position = item.get("position", "first")
        if position not in {"first", "last"}:
            raise ValueError(f"targets.json 第 {index} 条 position 只能是 first/last")
        for boolean_field in (
            "allow_ambiguous",
            "allow_duplicate_numbers",
            "disabled",
            "browser_fallback",
        ):
            if boolean_field in item and not isinstance(item[boolean_field], bool):
                raise ValueError(
                    f"targets.json 第 {index} 条 {boolean_field} 必须是布尔值"
                )
        keywords = item.get("keywords")
        if keywords is not None and (
''',
)

# Limit expensive browser fallbacks even when HTTP workers are high.
replace_once(
    "kill_numbers/acquisition/browser_pool.py",
    "from kill_numbers.acquisition.http_client import HEADERS\n",
    "import os\nimport threading\n\nfrom kill_numbers.acquisition.http_client import HEADERS\n",
)
replace_once(
    "kill_numbers/acquisition/browser_pool.py",
    '''def _render_page_parts(url: str, timeout: int) -> tuple[str, str]:
    from playwright.sync_api import sync_playwright

    page_url = remove_fragment(url)
    with sync_playwright() as playwright:
''',
    '''_BROWSER_CONCURRENCY = max(1, int(os.environ.get("SHUZI_BROWSER_CONCURRENCY", "2")))
_BROWSER_SEMAPHORE = threading.BoundedSemaphore(_BROWSER_CONCURRENCY)


def _render_page_parts(url: str, timeout: int) -> tuple[str, str]:
    from playwright.sync_api import sync_playwright

    page_url = remove_fragment(url)
    with _BROWSER_SEMAPHORE, sync_playwright() as playwright:
''',
)

# Thread pools should not swallow KeyboardInterrupt/SystemExit.
replace_all(
    "kill_numbers/application/batch_service.py",
    "except BaseException as exc:",
    "except Exception as exc:",
)

# ---------------------------------------------------------------------------
# 5. Entrypoints: single-period guard and stale multi-run output isolation.
# ---------------------------------------------------------------------------
replace_once(
    "run_crawler_prompt.py",
    '''def crawler_command_for_input(raw_issues: str) -> list[str]:
    cmd = [sys.executable, str(CRAWLER_FILE), "--workers", str(CRAWLER_WORKERS)]
    if raw_issues:
        cmd.extend(["--issues", raw_issues])
        if len(crawler.parse_issues(raw_issues)) > 1:
            cmd.append("--no-cache-update")
    return cmd
''',
    '''def crawler_command_for_input(raw_issues: str) -> list[str]:
    parsed = crawler.parse_issues(raw_issues or crawler.DEFAULT_ISSUES)
    if len(parsed) != 1:
        raise ValueError("单期入口只允许一个期数；多个期数请使用多期入口。")
    return [
        sys.executable,
        str(CRAWLER_FILE),
        "--workers",
        str(CRAWLER_WORKERS),
        "--issues",
        parsed[0],
    ]
''',
)
replace_once(
    "run_crawler_prompt.py",
    '''    cmd = crawler_command_for_input(issues)

    print()
''',
    '''    try:
        cmd = crawler_command_for_input(issues)
    except ValueError as exc:
        print(exc)
        return 2

    print()
''',
)

replace_once(
    "run_crawler_multi_prompt.py",
    "import re\n",
    "import hashlib\nimport re\nimport time\n",
)
replace_once(
    "run_crawler_multi_prompt.py",
    '''class IssueRun:
    issue: str
    returncode: int
    success_file: Path
    failed_file: Path
''',
    '''class IssueRun:
    issue: str
    returncode: int
    success_file: Path
    failed_file: Path
    success_fresh: bool
    failure_fresh: bool
''',
)
replace_once(
    "run_crawler_multi_prompt.py",
    '''def success_names(result_file: Path) -> set[str]:
    names = set()
''',
    '''def success_names(result_file: Path, *, fresh: bool = True) -> set[str]:
    names = set()
    if not fresh:
        return names
''',
)
replace_once(
    "run_crawler_multi_prompt.py",
    '''def failure_reasons(failed_file: Path) -> dict[str, str]:
    reasons = {}
''',
    '''def failure_reasons(failed_file: Path, *, fresh: bool = True) -> dict[str, str]:
    reasons = {}
    if not fresh:
        return reasons
''',
)
replace_once(
    "run_crawler_multi_prompt.py",
    '''        passed_names.update(success_names(run.success_file))
        reasons_by_issue[run.issue] = failure_reasons(run.failed_file)
''',
    '''        passed_names.update(success_names(run.success_file, fresh=run.success_fresh))
        reasons_by_issue[run.issue] = failure_reasons(
            run.failed_file,
            fresh=run.failure_fresh,
        )
''',
)
regex_once(
    "run_crawler_multi_prompt.py",
    r'''def run_issue\(issue: str\) -> IssueRun:.*?\n(?=def _main_unlocked\()''',
    '''def _file_signature(path: Path) -> tuple[int, int, str] | None:
    if not path.exists():
        return None
    data = path.read_bytes()
    stat = path.stat()
    return stat.st_mtime_ns, len(data), hashlib.sha256(data).hexdigest()


def run_issue(issue: str) -> IssueRun:
    result_file, failed_file, _report_file = crawler.output_files_for_issues([issue])
    success_path = Path(result_file)
    failure_path = Path(failed_file)
    before_success = _file_signature(success_path)
    before_failure = _file_signature(failure_path)
    started_ns = time.time_ns()

    cmd = [
        sys.executable,
        str(CRAWLER_FILE),
        "--workers",
        str(CRAWLER_WORKERS),
        "--issues",
        issue,
        "--no-cache-update",
    ]
    print()
    print(f"开始抓取 {issue}期")
    result = subprocess.run(cmd, cwd=BASE_DIR)
    after_success = _file_signature(success_path)
    after_failure = _file_signature(failure_path)

    success_fresh = bool(
        after_success
        and after_success != before_success
        and after_success[0] >= started_ns
    )
    failure_fresh = bool(
        after_failure
        and after_failure != before_failure
        and after_failure[0] >= started_ns
    )
    return IssueRun(
        issue=issue,
        returncode=result.returncode,
        success_file=success_path,
        failed_file=failure_path,
        success_fresh=success_fresh,
        failure_fresh=failure_fresh,
    )


''',
)

# ---------------------------------------------------------------------------
# 6. Duplicate checker: current output format, complete cache, fail-closed report.
# ---------------------------------------------------------------------------
replace_once(
    "check_duplicates.py",
    '''def cache_site_key(name: str, url: str) -> tuple[str, str]:
    return ("url", url.strip()) if url.strip() else ("name", name.strip())
''',
    '''def cache_site_key(name: str, url: str) -> tuple[str, str]:
    normalized_url = url.strip().lower()
    return ("url", normalized_url) if normalized_url else ("name", name.strip())
''',
)

regex_once(
    "check_duplicates.py",
    r'''def parse_line\(.*?\n(?=def read_records\()''',
    '''def parse_line(
    line: str,
    source_file: str,
    line_no: int,
    default_issue: str = "",
) -> Record | None:
    raw = line.rstrip("\\n")
    if not raw.strip():
        return None

    explicit = re.match(
        r"^\\s*([0-9０-９,，.．\\s]+?)\\s+(.+?)\\s+(\\d+\\s*期)\\s*$",
        raw,
    )
    if explicit:
        numbers = normalize_numbers(explicit.group(1))
        name = re.sub(r"\\s+", " ", explicit.group(2).strip())
        issue = re.sub(r"\\s+", "", explicit.group(3))
        return Record(source_file, line_no, numbers, name, issue, raw)

    if default_issue:
        implicit = re.match(r"^\\s*([0-9０-９,，.．\\s]+?)\\s+(.+?)\\s*$", raw)
        if implicit:
            numbers = normalize_numbers(implicit.group(1))
            name = re.sub(r"\\s+", " ", implicit.group(2).strip())
            return Record(
                source_file,
                line_no,
                numbers,
                name,
                f"{issue_key(default_issue)}期",
                raw,
            )
    return None


''',
)
replace_once(
    "check_duplicates.py",
    '''        for line_no, line in enumerate(lines, start=1):
            record = parse_line(line, path.name, line_no)
''',
    '''        filename_match = re.match(r"^(\\d+)期-杀数字-成功\\.txt$", path.name)
        default_issue = filename_match.group(1) if filename_match else ""
        for line_no, line in enumerate(lines, start=1):
            record = parse_line(line, path.name, line_no, default_issue=default_issue)
''',
)

replace_once(
    "check_duplicates.py",
    '''def recent_count_for_snapshot(snapshot: TargetSnapshot, fallback_recent_count: int) -> int:
    target = snapshot.target or {}
    name = str(target.get("name") or snapshot.name or "")
    url = str(target.get("url") or snapshot.url or "")
    if name in SPECIAL_RECENT_COUNTS or "a.am6w.com/bbs1.aspx?id=sha04" in url:
        return SPECIAL_RECENT_COUNTS.get(name, 8)
    if "msbqxti.zhx2n-7v5x3-ivdpud.xyz:16677" in url:
        return 9
    if "lx11.www87127b.com:8443/bbs/103.html" in url:
        return 8
    return fallback_recent_count
''',
    '''def recent_count_for_snapshot(snapshot: TargetSnapshot, fallback_recent_count: int) -> int:
    _ = snapshot
    # Formal duplicate conclusions always require the configured full window.
    return fallback_recent_count
''',
)
replace_once(
    "check_duplicates.py",
    '''        if not issues:
            problems.append(CrawlProblem(snapshot.name, snapshot.url, "本页同栏目没有识别到可用期数"))
            print(f"[{done_count}/{len(snapshots)}] 无数据：{snapshot.url}")
            continue

        found, failure = records_from_snapshot(snapshot, issues)
''',
    '''        if not issues:
            problems.append(CrawlProblem(snapshot.name, snapshot.url, "本页同栏目没有识别到可用期数"))
            print(f"[{done_count}/{len(snapshots)}] 无数据：{snapshot.url}")
            continue
        if len(issues) != site_recent_count:
            problems.append(
                CrawlProblem(
                    snapshot.name,
                    snapshot.url,
                    f"近{site_recent_count}期数据不足，实际只有{len(issues)}期",
                )
            )
            print(f"[{done_count}/{len(snapshots)}] 数据不足：{snapshot.url}")
            continue

        found, failure = records_from_snapshot(snapshot, issues)
''',
)

regex_once(
    "check_duplicates.py",
    r'''def validate_cache_data\(.*?\n(?=def write_records_cache\()''',
    '''def validate_cache_data(
    data: object,
    path: Path,
    expected_recent_count: int | None = None,
    targets: list[dict] | None = None,
) -> list[dict]:
    if not isinstance(data, dict) or data.get("version") not in {1, 2}:
        raise ValueError(f"缓存文件结构或版本错误：{path}")
    version = int(data["version"])
    if not isinstance(data.get("generated_at"), str) or not data["generated_at"].strip():
        raise ValueError(f"缓存文件 generated_at 无效：{path}")

    recent_count = data.get("recent_count")
    if isinstance(recent_count, bool) or not isinstance(recent_count, int) or recent_count <= 0:
        raise ValueError(f"缓存文件 recent_count 无效：{path}")
    if expected_recent_count is not None and recent_count != expected_recent_count:
        raise ValueError(
            f"缓存文件 recent_count 不匹配：期望 {expected_recent_count}，实际 {recent_count}"
        )

    records = data.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError(f"缓存文件 records 缺失或为空：{path}")
    failure_records = data.get("failures", [])
    if not isinstance(failure_records, list):
        raise ValueError(f"缓存文件 failures 格式错误：{path}")

    success_by_key: dict[tuple[tuple[str, str], str], dict] = {}
    available_sites: set[tuple[str, str]] = set()
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
        key = (site, issue)
        normalized_numbers = normalize_numbers(numbers)
        previous = success_by_key.get(key)
        if previous is not None and normalize_numbers(previous["numbers"]) != normalized_numbers:
            raise ValueError(f"缓存文件同站同期冲突：{name} {issue}期")
        success_by_key[key] = item
        available_sites.add(site)

    failure_by_key: dict[tuple[tuple[str, str], str], dict] = {}
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
            or status != "failed"
            or not isinstance(reason, str)
            or not reason.strip()
            or not issue
            or "numbers" in item
        ):
            raise ValueError(f"缓存文件第 {index} 条失败状态无效：{path}")
        key = (cache_site_key(name, url), issue)
        failure_by_key[key] = item
        available_sites.add(key[0])

    overlap = set(success_by_key) & set(failure_by_key)
    if overlap:
        raise ValueError(f"缓存文件同站同期同时存在成功和失败：{sorted(overlap)!r}")

    timeline_by_site: dict[tuple[str, str], list[dict]] = defaultdict(list)
    if version == 2:
        timeline = data.get("timeline")
        if not isinstance(timeline, list) or not timeline:
            raise ValueError(f"缓存版本2缺少 timeline：{path}")
        seen_periods: set[tuple[tuple[str, str], int, str]] = set()
        for index, entry in enumerate(timeline, start=1):
            if not isinstance(entry, dict):
                raise ValueError(f"缓存 timeline 第 {index} 条无效：{path}")
            name = entry.get("name")
            url = entry.get("url")
            issue = issue_key(entry.get("issue", ""))
            status = entry.get("status")
            sequence = entry.get("sequence")
            cycle = entry.get("cycle")
            if (
                not isinstance(name, str)
                or not name.strip()
                or not isinstance(url, str)
                or not issue
                or status not in {"success", "failed"}
                or isinstance(sequence, bool)
                or not isinstance(sequence, int)
                or sequence <= 0
                or isinstance(cycle, bool)
                or not isinstance(cycle, int)
                or cycle < 0
            ):
                raise ValueError(f"缓存 timeline 第 {index} 条字段无效：{path}")
            site = cache_site_key(name, url)
            period = (site, cycle, issue)
            if period in seen_periods:
                raise ValueError(f"缓存 timeline 同站同期冲突：{name} cycle={cycle} issue={issue}")
            seen_periods.add(period)
            payload_key = (site, issue)
            if status == "success" and payload_key not in success_by_key:
                raise ValueError(f"缓存 timeline 成功状态缺少号码：{name} {issue}期")
            if status == "failed" and payload_key not in failure_by_key:
                raise ValueError(f"缓存 timeline 失败状态缺少原因：{name} {issue}期")
            timeline_by_site[site].append(entry)
    elif targets is not None:
        raise ValueError(f"缓存版本1缺少周期和顺序证据，正式判重前必须重建：{path}")

    if targets is not None:
        enabled_sites = {
            cache_site_key(target_name(target), str(target.get("url") or ""))
            for target in targets
        }
        missing = [
            target_name(target)
            for target in targets
            if cache_site_key(target_name(target), str(target.get("url") or "")) not in available_sites
        ]
        if missing:
            raise ValueError(f"缓存文件缺少启用目标：{', '.join(missing)}")
        unexpected_sites = available_sites - enabled_sites
        if unexpected_sites:
            raise ValueError(f"缓存文件包含未启用目标：{sorted(unexpected_sites)!r}")

        for target in targets:
            name = target_name(target)
            url = str(target.get("url") or "")
            site = cache_site_key(name, url)
            entries = sorted(
                timeline_by_site.get(site, []),
                key=lambda entry: int(entry["sequence"]),
            )
            if len(entries) != recent_count:
                raise ValueError(
                    f"缓存文件 {name} 近{recent_count}期不完整：实际 {len(entries)} 期"
                )
            if any(entry["status"] != "success" for entry in entries):
                raise ValueError(f"缓存文件 {name} 近{recent_count}期含失败状态，检测未完成")

            expected_count = target.get("count")
            previous_issue: int | None = None
            previous_cycle: int | None = None
            for entry in entries:
                issue = issue_key(entry["issue"])
                record = success_by_key[(site, issue)]
                tokens = re.findall(r"\\d{1,2}", normalize_numbers(record["numbers"]))
                if expected_count and len(tokens) != expected_count:
                    raise ValueError(
                        f"缓存文件 {name} {issue}期号码数量 {len(tokens)}，配置要求 {expected_count}"
                    )
                if any(not 1 <= int(token) <= 49 for token in tokens):
                    raise ValueError(f"缓存文件 {name} {issue}期包含01至49以外号码")
                if not target.get("allow_duplicate_numbers", False) and len(tokens) != len(set(tokens)):
                    raise ValueError(f"缓存文件 {name} {issue}期号码重复")

                current_issue = int(issue)
                current_cycle = int(entry["cycle"])
                if previous_issue is not None:
                    normal_next = current_cycle == previous_cycle and current_issue == previous_issue + 1
                    wrapped_next = (
                        current_cycle == previous_cycle + 1
                        and previous_issue >= 360
                        and current_issue <= 5
                    )
                    if not normal_next and not wrapped_next:
                        raise ValueError(
                            f"缓存文件 {name} 期号不连续：{previous_issue}期 -> {current_issue}期"
                        )
                previous_issue = current_issue
                previous_cycle = current_cycle
    return records


''',
)

regex_once(
    "check_duplicates.py",
    r'''def write_records_cache\(.*?\n(?=def load_records_cache\()''',
    '''def write_records_cache(
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

    payload_records = [
        {
            "name": record.name,
            "url": record.url,
            "issue": issue_key(record.issue),
            "numbers": record.numbers,
        }
        for record in records
    ]
    by_site: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for record in payload_records:
        by_site[cache_site_key(record["name"], record["url"])].append(record)

    timeline: list[dict] = []
    sequence = 0
    for site_records in by_site.values():
        cycle = 0
        previous_issue: int | None = None
        for record in site_records:
            current_issue = int(issue_key(record["issue"]))
            if previous_issue is not None and previous_issue - current_issue >= 100:
                cycle += 1
            elif previous_issue is not None and current_issue < previous_issue:
                raise ValueError(
                    f"{record['name']} 期号顺序无法确认：{previous_issue}期 -> {current_issue}期"
                )
            sequence += 1
            timeline.append(
                {
                    "name": record["name"],
                    "url": record["url"],
                    "issue": record["issue"],
                    "status": "success",
                    "sequence": sequence,
                    "cycle": cycle,
                }
            )
            previous_issue = current_issue

    data = {
        "version": 2,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "recent_count": recent_count,
        "run_sequence": sequence,
        "records": payload_records,
        "failures": [],
        "timeline": timeline,
    }
    validate_cache_data(data, path, expected_recent_count=recent_count, targets=targets)
    atomic_write_json(path, data, trailing_newline=False)


''',
)

# Insert explicit report completeness state in v2 report only.
content = read("check_duplicates.py")
marker = '''def write_report_v2(
'''
start = content.find(marker)
if start < 0:
    raise RuntimeError("check_duplicates.py: write_report_v2 not found")
list_marker = '''    lines: list[str] = []
'''
pos = content.find(list_marker, start)
if pos < 0:
    raise RuntimeError("check_duplicates.py: write_report_v2 lines marker not found")
insert = '''    complete = not problems and not region_problems and not bad_lines
    lines: list[str] = []
    lines.append(f"检测状态：{'完整' if complete else '检测未完成'}")
'''
content = content[:pos] + content[pos:].replace(list_marker, insert, 1)
write("check_duplicates.py", content)
replace_all(
    "check_duplicates.py",
    '''        lines.append("没有发现达到连续3期以上的同网站重复风险。")''',
    '''        lines.append(
            "没有发现达到连续3期以上的同网站重复风险。"
            if complete
            else "检测未完成，不能得出无重复结论。"
        )''',
)
replace_all(
    "check_duplicates.py",
    '''        lines.append("没有发现同一期整串重复。")''',
    '''        lines.append(
            "没有发现同一期整串重复。"
            if complete
            else "检测未完成，不能得出同一期无重复结论。"
        )''',
)
replace_all(
    "check_duplicates.py",
    '''        lines.append("没有发现跨期整串重复。")''',
    '''        lines.append(
            "没有发现跨期整串重复。"
            if complete
            else "检测未完成，不能得出跨期无重复结论。"
        )''',
)

replace_once(
    "check_duplicates.py",
    '''    print(f"完成：读取 {len(records)} 条，连续重复风险 {len(site_matches)} 组，同期重复 {len(duplicates)} 组，跨期重复 {len(cross_duplicates)} 组")
    print(f"报告：{args.output}")
    return 0
''',
    '''    print(f"完成：读取 {len(records)} 条，连续重复风险 {len(site_matches)} 组，同期重复 {len(duplicates)} 组，跨期重复 {len(cross_duplicates)} 组")
    print(f"报告：{args.output}")
    if problems or region_problems or bad_lines:
        return 4
    if any(item.status == "reject" for item in site_matches):
        return 6
    if any(item.status == "suspect" for item in site_matches):
        return 5
    return 0
''',
)
replace_once(
    "check_duplicates.py",
    '''    except RuntimeError as exc:
        print(str(exc))
        return 2
''',
    '''    except RuntimeError as exc:
        print(str(exc))
        return 2
    except (OSError, ValueError) as exc:
        print(f"检测未完成：{exc}")
        return 4
''',
)

# --latest is an explicit global diagnostic and must never rebuild formal cache.
replace_once(
    "check_duplicates.py",
    '''        if args.issues and args.latest:
            parser.error("--issues 和 --latest 只能二选一")
''',
    '''        if args.issues and args.latest:
            parser.error("--issues 和 --latest 只能二选一")
        if args.latest and args.write_cache:
            parser.error("--latest 使用全局期数，禁止据此覆盖正式缓存；请不带 --latest 运行按站点检测")
''',
)

# ---------------------------------------------------------------------------
# 7. Regression tests for every critical invariant fixed above.
# ---------------------------------------------------------------------------
write(
    "tests/test_critical_audit_fixes.py",
    '''import json
from pathlib import Path

import pytest

import check_duplicates
import crawler
import run_crawler_prompt
from kill_numbers.acquisition.documents import make_source_document
from kill_numbers.domain.models import CrawlFailure, CrawlResult
from kill_numbers.infrastructure.cache_repository import update_recent_duplicate_cache
from kill_numbers.parsing.common import extract_issue_numbers, find_number_groups
from kill_numbers.validation.result_validator import (
    evidence_from_source_document,
    validate_crawl_results,
)


def _numbers(start: int, count: int = 3) -> list[str]:
    return [f"{value:02d}" for value in range(start, start + count)]


def test_configured_top_window_is_used_instead_of_fixed_three():
    content = "栏目起点\n" + "\n".join(
        f"{issue}期 专属栏目 {' '.join(_numbers(index * 3 + 1))}"
        for index, issue in enumerate([215, 214, 213, 212, 211])
    ) + "\n栏目终点"

    found = extract_issue_numbers(
        content,
        ["211"],
        keywords=["专属栏目"],
        expected_count=3,
        strict_ambiguous=True,
        anchor="栏目起点",
        stop_anchor="栏目终点",
        region="top",
        issue_position_window=5,
    )
    assert found["211"] == _numbers(13)

    missing = extract_issue_numbers(
        content,
        ["211"],
        keywords=["专属栏目"],
        expected_count=3,
        strict_ambiguous=True,
        anchor="栏目起点",
        stop_anchor="栏目终点",
        region="top",
        issue_position_window=4,
    )
    assert missing == {}


def test_invalid_token_invalidates_entire_number_group():
    assert find_number_groups("01 02 03 04 05 06 07 08 09 10 50") == []
    assert find_number_groups("00 01 02 03 04 05 06 07 08 09 10") == []


def test_configured_stop_anchor_is_required():
    with pytest.raises(ValueError, match="没有找到结束锚点"):
        extract_issue_numbers(
            "栏目起点\n211期 专属栏目 01 02 03",
            ["211"],
            keywords=["专属栏目"],
            expected_count=3,
            anchor="栏目起点",
            stop_anchor="栏目终点",
            region="top",
        )


def test_evidence_cannot_fall_back_to_whole_document_when_anchor_is_missing():
    target = {
        "url": "https://example.test/topic/1",
        "name": "测试站",
        "count": 3,
        "keywords": ["专属栏目"],
        "anchor": "不存在的栏目锚点",
        "region": "top",
    }
    document = make_source_document(
        kind="page",
        url=target["url"],
        content="211期 专属栏目 01 02 03",
        priority=100,
    )
    with pytest.raises(ValueError, match="来源锚点"):
        evidence_from_source_document(target, "211", ["01", "02", "03"], document)


def test_normalized_result_keeps_matching_evidence():
    target = {
        "url": "https://example.test/topic/1",
        "name": "测试站",
        "count": 3,
    }
    document = make_source_document(
        kind="page",
        url=target["url"],
        content="211期 01 02 03",
        priority=100,
    )
    evidence = evidence_from_source_document(target, "211", ["01", "02", "03"], document)
    accepted, reason = validate_crawl_results(
        target,
        ["211"],
        [CrawlResult(target["url"], target["name"], "211", ["1", "2", "3"], evidence)],
    )
    assert reason == ""
    assert accepted[0].numbers == ["01", "02", "03"]
    assert accepted[0].evidence is evidence


def test_cache_retention_is_per_site_not_global_issue(tmp_path):
    path = tmp_path / "recent_10_cache.json"
    records = []
    timeline = []
    sequence = 0
    for name, url, first in [
        ("站点A", "https://a.test", 211),
        ("站点B", "https://b.test", 206),
    ]:
        for issue in range(first, first + 10):
            sequence += 1
            records.append(
                {"name": name, "url": url, "issue": str(issue), "numbers": "01,02,03"}
            )
            timeline.append(
                {
                    "name": name,
                    "url": url,
                    "issue": str(issue),
                    "status": "success",
                    "sequence": sequence,
                    "cycle": 0,
                }
            )
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "generated_at": "2026-09-09T00:00:00",
                "recent_count": 10,
                "run_sequence": sequence,
                "records": records,
                "failures": [],
                "timeline": timeline,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    update_recent_duplicate_cache(
        path,
        [CrawlResult("https://a.test", "站点A", "221", ["04", "05", "06"])],
        ["221"],
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    b_issues = sorted(
        int(item["issue"]) for item in data["records"] if item["name"] == "站点B"
    )
    assert b_issues == list(range(206, 216))


def test_cache_success_clears_old_failure_and_same_run_conflict_is_rejected(tmp_path):
    path = tmp_path / "recent_10_cache.json"
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "generated_at": "2026-09-09T00:00:00",
                "recent_count": 10,
                "run_sequence": 1,
                "records": [],
                "failures": [
                    {
                        "name": "站点A",
                        "url": "https://a.test",
                        "issue": "211",
                        "status": "failed",
                        "reason": "旧失败",
                    }
                ],
                "timeline": [
                    {
                        "name": "站点A",
                        "url": "https://a.test",
                        "issue": "211",
                        "status": "failed",
                        "sequence": 1,
                        "cycle": 0,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    update_recent_duplicate_cache(
        path,
        [CrawlResult("https://a.test", "站点A", "211", ["01", "02", "03"])],
        ["211"],
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["failures"] == []
    assert data["records"][0]["numbers"] == "01,02,03"

    with pytest.raises(ValueError, match="同时返回成功和失败"):
        update_recent_duplicate_cache(
            path,
            [CrawlResult("https://a.test", "站点A", "212", ["01", "02", "03"])],
            ["212"],
            failures=[CrawlFailure("https://a.test", "站点A", "本轮失败")],
        )


def test_formal_cache_validation_requires_full_ten_periods(tmp_path):
    path = tmp_path / "cache.json"
    records = [
        {"name": "站点A", "url": "https://a.test", "issue": str(issue), "numbers": "01,02,03"}
        for issue in range(203, 212)
    ]
    timeline = [
        {
            "name": "站点A",
            "url": "https://a.test",
            "issue": str(issue),
            "status": "success",
            "sequence": index,
            "cycle": 0,
        }
        for index, issue in enumerate(range(203, 212), start=1)
    ]
    data = {
        "version": 2,
        "generated_at": "2026-09-09T00:00:00",
        "recent_count": 10,
        "run_sequence": 9,
        "records": records,
        "failures": [],
        "timeline": timeline,
    }
    with pytest.raises(ValueError, match="近10期不完整"):
        check_duplicates.validate_cache_data(
            data,
            path,
            expected_recent_count=10,
            targets=[
                {
                    "name": "站点A",
                    "url": "https://a.test",
                    "count": 3,
                    "region": "top",
                }
            ],
        )


def test_current_success_file_format_uses_issue_from_filename(tmp_path):
    path = tmp_path / "211期-杀数字-成功.txt"
    path.write_text("01,02,03 测试站\n", encoding="utf-8")
    records, bad = check_duplicates.read_records([path])
    assert bad == []
    assert len(records) == 1
    assert records[0].issue == "211期"
    assert records[0].name == "测试站"


def test_single_prompt_rejects_multiple_issues():
    with pytest.raises(ValueError, match="单期入口只允许一个期数"):
        run_crawler_prompt.crawler_command_for_input("211,212")
''',
)

print("critical audit fixes applied")
