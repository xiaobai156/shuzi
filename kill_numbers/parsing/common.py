import re
from dataclasses import dataclass
from collections.abc import Iterable

from kill_numbers.text_utils import (
    as_list,
    clean_name,
    compact_text,
    html_to_text,
    normalize_issue,
    normalize_keyword,
    unique_keep_order,
)
from kill_numbers.parsing.errors import (
    AmbiguousSourceError,
    CandidateConflictError,
    NoCandidateError,
    SourceContractError,
)


# Default only: an explicitly configured window is a business contract.
CANDIDATE_REGION_WINDOW = 5


def resolve_candidate_window(value: object = None) -> int:
    if value is None:
        return CANDIDATE_REGION_WINDOW
    if type(value) is not int or value <= 0:
        raise ValueError(f"issue_position_window 必须是正整数：{value!r}")
    return value



def target_candidate_window(target: dict) -> int:
    default = 3 if target.get("special_parser") in {"zuibaxian_top7", "fengwu_jiutian_bottom_10"} else None
    return resolve_candidate_window(target.get("issue_position_window", default))


@dataclass(frozen=True)
class CandidateRow:
    issue: str
    groups: tuple[tuple[str, ...], ...]
    start: int
    end: int
    segment: str


@dataclass(frozen=True)
class AnchorScope:
    """One complete configured anchor/stop block in a single document."""

    anchor: str
    start: int
    end: int
    text: str


def candidate_rows(text, keywords=None, expected_count=None, allow_duplicate_numbers=False):
    """One slot per valid logical row, not per number group or unvalidated post."""
    keyword_list = [normalize_keyword(k) for k in (keywords or []) if k]
    for match in iter_all_issue_segment_matches(text):
        segment = match.group(0)
        if keyword_list and not any(k in normalize_keyword(segment) for k in keyword_list):
            continue
        groups = []
        for group in find_number_groups(segment):
            if expected_count is not None and len(group) != expected_count:
                continue
            if not allow_duplicate_numbers and has_duplicate_numbers(group):
                continue
            group = tuple(group)
            if group not in groups:
                groups.append(group)
        if groups:
            yield CandidateRow(normalize_issue(match.group(1)), tuple(groups),
                               match.start(), match.end(), segment)


def windowed_rows(rows, region, window=None):
    values = list(rows)
    size = resolve_candidate_window(window)
    direction = normalize_region(region)
    if direction == "top":
        return values[:size]
    if direction == "bottom":
        return values[-size:]
    return values

BROAD_KEYWORD_MARKERS = (
    "杀",
    "码",
    "十码",
    "10码",
    "五码",
    "六码",
    "七码",
)


def target_keywords(target: dict) -> list[str]:
    value = target.get("keywords") or []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value if item]

def has_broad_keywords(target: dict) -> bool:
    normalized = "".join(compact_text(keyword) for keyword in target_keywords(target))
    return any(marker in normalized for marker in BROAD_KEYWORD_MARKERS)

def manual_risk_reason(target: dict) -> str:
    reasons = []
    if not target.get("anchor"):
        reasons.append("缺少 anchor")
    if not target.get("anchor") and has_broad_keywords(target) and not target.get("special_parser"):
        reasons.append("宽关键词")
    if target.get("allow_ambiguous"):
        reasons.append("allow_ambiguous")
    return "、".join(reasons)

def candidate_anchor_values(text: str, target: dict) -> list[str]:
    name = clean_name(target.get("name") or "")
    candidates = []
    if name and name != "未命名":
        candidates.extend(
            [
                name,
                f"作者:{name}",
                f"作者：{name}",
                f"作者\n{name}",
                f"{name} 发表于",
                f"[{name}]楼主",
                f"【{name}】",
            ]
        )

    text_value = html_to_text(text)
    compact_name = compact_text(name)
    for line in text_value.splitlines():
        compact_line = compact_text(line)
        if compact_name and compact_name in compact_line and len(compact_line) <= max(8, len(compact_name) + 16):
            candidates.append(line.strip())

    return unique_keep_order(candidates)

def find_dedicated_anchor_candidate(text: str, target: dict, issues: list[str]) -> str | None:
    try:
        base_found = extract_issue_numbers(
            text,
            issues,
            keywords=target.get("keywords"),
            expected_count=target.get("count"),
            position=target.get("position", "first"),
            strict_ambiguous=True,
            allow_duplicate_numbers=target.get("allow_duplicate_numbers", False),
            anchor=target.get("anchor"),
            stop_anchor=target.get("stop_anchor"),
            region=target.get("region"),
            issue_position_window=target.get("issue_position_window"),
        )
    except NoCandidateError:
        return None
    if not base_found:
        return None

    for anchor in candidate_anchor_values(text, target):
        if find_anchor_index(html_to_text(text), anchor) < 0:
            continue
        try:
            anchored_found = extract_issue_numbers(
                text,
                issues,
                keywords=target.get("keywords"),
                expected_count=target.get("count"),
                position=target.get("position", "first"),
                strict_ambiguous=True,
                allow_duplicate_numbers=target.get("allow_duplicate_numbers", False),
                anchor=anchor,
                stop_anchor=target.get("stop_anchor"),
                region=target.get("region"),
                issue_position_window=target.get("issue_position_window"),
            )
        except NoCandidateError:
            continue
        if anchored_found and anchored_found == base_found:
            return anchor
    return None

def valid_number(token: str) -> bool:
    try:
        number = int(token)
    except ValueError:
        return False
    return 1 <= number <= 49

def find_number_groups(segment: str) -> list[list[str]]:
    before_open = re.split(r"[=＝]?\s*开\s*[:：]?", segment, maxsplit=1)[0]

    def collect(source: str) -> list[list[str]]:
        found = []
        # Match the ENTIRE numeric run. Never trim 00/50 or a third digit to
        # manufacture a valid count from an invalid group.
        for match in re.finditer(
            r"(?<!\d)\d+(?:[\s.,，。、;；|/\\-]+\d+){2,}(?!\d)",
            source.replace("杀", " "),
        ):
            nums = re.findall(r"\d+", match.group(0))
            if any(len(num) != 2 or not valid_number(num) for num in nums):
                continue
            found.append(nums)
        return found

    groups = collect(before_open)
    if not groups:
        # Supported template: a pending/open marker followed by a bracketed row.
        groups = collect(segment)
    return sorted(groups, key=len, reverse=True)

def has_duplicate_numbers(numbers: list[str]) -> bool:
    return len(numbers) != len(set(numbers))

def has_pending_open_marker(segment: str) -> bool:
    return bool(re.search(r"开\s*[:：]?\s*(?:[?？]{1,2}|0{2,4})", segment))

def select_candidate(
    candidates: list[tuple[list[str], str]],
    position: str = "first",
    strict_ambiguous: bool = False,
    issue: str = "",
    allow_duplicate_numbers: bool = False,
) -> list[str] | None:
    if allow_duplicate_numbers:
        valid_candidates = candidates
    else:
        valid_candidates = [
            (numbers, segment)
            for numbers, segment in candidates
            if not has_duplicate_numbers(numbers)
        ]
    if not valid_candidates:
        return None

    distinct: list[tuple[list[str], str]] = []
    seen = set()
    for numbers, segment in valid_candidates:
        key = tuple(numbers)
        if key not in seen:
            seen.add(key)
            distinct.append((numbers, segment))

    if len(distinct) == 1:
        return distinct[0][0]

    if strict_ambiguous:
        preview = " | ".join(",".join(numbers) for numbers, _ in distinct[:5])
        issue_text = f"{issue}期" if issue else "该期"
        raise CandidateConflictError(
            f"{issue_text} 候选不唯一，已停止输出避免抓错：{preview}"
        )

    pending = [
        (numbers, segment)
        for numbers, segment in distinct
        if has_pending_open_marker(segment)
    ]
    if len(pending) == 1:
        return pending[0][0]

    return distinct[-1][0] if position == "last" else distinct[0][0]

def issue_segment_matches(text: str, issue: str) -> list[re.Match]:
    issue = normalize_issue(issue)
    pattern = re.compile(
        rf"(?<!\d)0?{re.escape(issue)}\s*期(?P<body>.*?)(?=(?<!\d)0?\d{{1,3}}\s*期|$)",
        re.S,
    )
    return list(pattern.finditer(text))

def issue_segments(text: str, issue: str) -> list[str]:
    return [m.group(0) for m in issue_segment_matches(text, issue)]

def iter_all_issue_segment_matches(text: str) -> Iterable[re.Match]:
    pattern = re.compile(
        r"(?<!\d)0?(\d{1,3})\s*期(?P<body>.*?)(?=(?<!\d)0?\d{1,3}\s*期|$)",
        re.S,
    )
    matches = pattern.finditer(text)
    first_match = next(matches, None)
    if first_match is not None:
        yield first_match
        yield from matches
        return

    fallback = re.compile(
        r"(?<!\d)0?(\d{2,3})\s*期(?P<body>.*?)(?=(?:\n\s*\d{1,3}[.,、\s])|$)",
        re.S,
    )
    yield from fallback.finditer(text)

def all_issue_segment_matches(text: str) -> list[re.Match]:
    return list(iter_all_issue_segment_matches(text))

def find_anchor_index(text: str, anchor: str, start: int = 0) -> int:
    direct = text.find(anchor, start)
    if direct >= 0:
        return direct

    normalized_anchor = normalize_keyword(anchor)
    if not normalized_anchor:
        return -1

    offset = 0
    for line in text.splitlines(keepends=True):
        if offset >= start and normalized_anchor in normalize_keyword(line):
            return offset
        offset += len(line)
    return -1


def find_anchor_positions(text: str, anchor: str, start: int = 0) -> list[int]:
    """Return every plausible section occurrence without choosing the first.

    Short non-period lines are the strongest heading candidates.  When a site
    places its heading and first period on one line, fall back to exact or
    normalized line occurrences so the caller can compare all complete blocks.
    """
    if not anchor:
        return []
    normalized_anchor = normalize_keyword(anchor)
    if not normalized_anchor:
        return []

    heading_positions: list[int] = []
    normalized_line_positions: list[int] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        compact = normalize_keyword(line)
        if offset >= start and normalized_anchor in compact:
            normalized_line_positions.append(offset)
            if not re.search(r"(?<!\d)0?\d{1,3}\s*期", line):
                # Navigation mentions, recommendation headings and the actual
                # section heading are all candidates.  The caller compares the
                # complete scoped results instead of trusting the first match.
                heading_positions.append(offset)
        offset += len(line)
    if heading_positions:
        return list(dict.fromkeys(heading_positions))

    exact_positions: list[int] = []
    cursor = max(0, start)
    while True:
        position = text.find(anchor, cursor)
        if position < 0:
            break
        exact_positions.append(position)
        cursor = position + max(1, len(anchor))
    if exact_positions:
        return exact_positions
    return list(dict.fromkeys(normalized_line_positions))


def anchor_scope_candidates(
    text: str,
    anchors=None,
    stop_anchors=None,
) -> list[AnchorScope]:
    """Return every complete configured anchor/stop block in one document.

    Missing anchors mean the document is unrelated.  Once any configured start
    anchor exists, a missing configured stop is a hard source-contract failure
    unless another occurrence forms a complete block.  Alternative anchors are
    all evaluated; no alias is allowed to hide a conflicting block.
    """
    anchor_list = as_list(anchors)
    if not anchor_list:
        return [AnchorScope("", 0, len(text), text)]

    stops = as_list(stop_anchors)
    scopes_by_bounds: dict[tuple[int, int], AnchorScope] = {}
    saw_start = False
    for anchor in anchor_list:
        starts = find_anchor_positions(text, anchor)
        if starts:
            saw_start = True
        for start_index in starts:
            end_positions = [
                find_anchor_index(
                    text,
                    stop,
                    start=start_index + max(1, len(anchor)),
                )
                for stop in stops
            ]
            end_positions = [end for end in end_positions if end >= 0]
            if stops and not end_positions:
                continue
            end_index = min(end_positions) if end_positions else len(text)
            if end_index <= start_index:
                continue
            key = (start_index, end_index)
            scopes_by_bounds.setdefault(
                key,
                AnchorScope(anchor, start_index, end_index, text[start_index:end_index]),
            )

    scopes = sorted(scopes_by_bounds.values(), key=lambda item: (item.start, item.end))
    if scopes:
        return scopes
    if saw_start and stops:
        raise SourceContractError(f"没有找到正文结束锚点：{stops}")
    raise NoCandidateError(f"没有找到正文锚点：{anchor_list}")

def find_last_anchor_index_before(text: str, anchor: str, end: int) -> int:
    direct = text.rfind(anchor, 0, end)
    if direct >= 0:
        return direct

    normalized_anchor = normalize_keyword(anchor)
    if not normalized_anchor:
        return -1

    candidate = -1
    offset = 0
    for line in text.splitlines(keepends=True):
        if offset >= end:
            break
        if normalized_anchor in normalize_keyword(line):
            candidate = offset
        offset += len(line)
    return candidate

def scope_text_by_anchor(
    text: str,
    anchors=None,
    stop_anchors=None,
) -> str:
    scoped, _start_index = scope_text_by_anchor_with_offset(text, anchors, stop_anchors)
    return scoped

def scope_text_by_anchor_with_offset(
    text: str,
    anchors=None,
    stop_anchors=None,
) -> tuple[str, int]:
    scopes = anchor_scope_candidates(text, anchors, stop_anchors)
    if len(scopes) != 1:
        raise AmbiguousSourceError(
            f"正文锚点对应 {len(scopes)} 个完整区块，调用方必须按候选内容判定唯一来源"
        )
    return scopes[0].text, scopes[0].start

def normalize_region(region: str | None) -> str:
    value = normalize_keyword(region or "").lower()
    if value in {"top", "upper", "head", "first", "上", "顶部"}:
        return "top"
    if value in {"bottom", "lower", "tail", "last", "下", "尾部", "底部"}:
        return "bottom"
    return ""

def filter_candidates_by_region(
    candidates: list[tuple[list[str], str, int]],
    region: str | None,
    text_length: int | None = None,
    strict_window: bool = False,
    require_region: bool = False,
    issue_position_window: int | None = None,
):
    region = normalize_region(region)
    if require_region and not region:
        return []
    if not region:
        return candidates

    ordered = sorted(candidates, key=lambda item: item[2])
    return windowed_rows(ordered, region, issue_position_window)

def needs_strict_region_window(candidates: list[tuple[list[str], str, int]]) -> bool:
    # Multiple candidates for one issue are always evaluated inside the same
    # fixed directional window before ambiguity is considered.
    return len(candidates) > 1

def issue_position_window_starts(
    text: str,
    keywords: list[str] | None,
    expected_count: int | None,
    region: str | None,
    issue_position_window: int | None,
    allow_duplicate_numbers: bool = False,
) -> set[int] | None:
    if not normalize_region(region):
        return None
    rows = candidate_rows(text, keywords, expected_count, allow_duplicate_numbers)
    return {row.start for row in windowed_rows(rows, region, issue_position_window)}


def extract_issue_numbers(
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
    allowed_row_starts: set[int] | None = None,
) -> dict[str, list[str]]:
    full_text = html_to_text(text)
    scopes = anchor_scope_candidates(full_text, anchor, stop_anchor)
    scope_results: list[tuple[AnchorScope, dict[str, list[str]]]] = []
    for scope in scopes:
        rows = list(
            candidate_rows(
                scope.text,
                keywords,
                expected_count,
                allow_duplicate_numbers,
            )
        )
        selected_rows = (
            [row for row in rows if row.start in allowed_row_starts]
            if allowed_row_starts is not None
            else windowed_rows(rows, region, issue_position_window)
        )
        found: dict[str, list[str]] = {}
        for raw_issue in issues:
            issue = normalize_issue(raw_issue)
            candidates = [
                (list(group), row.segment)
                for row in selected_rows
                if row.issue == issue
                for group in row.groups
            ]
            if candidates:
                selected = select_candidate(
                    candidates,
                    position=position,
                    strict_ambiguous=True,
                    issue=issue,
                    allow_duplicate_numbers=allow_duplicate_numbers,
                )
                if selected:
                    found[issue] = selected
        if found:
            scope_results.append((scope, found))

    if not scope_results:
        return {}
    signatures = {
        tuple((issue, tuple(numbers)) for issue, numbers in sorted(found.items()))
        for _scope, found in scope_results
    }
    if len(signatures) > 1:
        raise AmbiguousSourceError(
            "正文锚点对应多个不同候选区块，已停止输出避免抓错"
        )
    # Identical results may appear in a navigation copy and the actual block.
    # Prefer the tightest complete scope, then the earliest deterministic one.
    _scope, found = min(
        scope_results,
        key=lambda item: (item[0].end - item[0].start, item[0].start),
    )
    return found
