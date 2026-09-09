import re
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


# Default direction boundary. A target-level issue_position_window overrides it.
CANDIDATE_REGION_WINDOW = 5


def resolve_candidate_window(value: object) -> int:
    if value is None:
        return CANDIDATE_REGION_WINDOW
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"issue_position_window 必须是正整数，实际为：{value!r}")
    return value
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
    except Exception:
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
        except Exception:
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
    before_open = before_open.replace("杀", " ")

    def collect(source: str) -> list[list[str]]:
        found = []
        # 每个 match 都是一组独立号码。禁止把多组、开奖号、日期或其他栏目拼接来凑 count。
        # 优先抓一串 2 位数，分隔符可以是点、空格、逗号、顿号等。
        # 分隔符必须存在，避免把日期 2020-10-02 19:41:06 当成号码。
        for match in re.finditer(
            r"(?<!\d)\d{2}(?:[\s.,，。、;；|/\\]+\d{2}){2,}",
            source,
        ):
            nums = re.findall(r"\d{2}", match.group(0))
            # Never repair an invalid group by deleting 00/>49 tokens.  One
            # invalid token invalidates the whole source group.
            if len(nums) >= 3 and all(valid_number(num) for num in nums):
                found.append(nums)
        return found

    groups = collect(before_open)
    if not groups:
        # 有些页面把“开:00准”放在第一行，号码放到下一行。
        groups = collect(segment.replace("杀", " "))

    groups.sort(key=len, reverse=True)
    return groups

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
        raise ValueError(f"{issue_text} 候选不唯一，已停止输出避免抓错：{preview}")

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
    anchor_list = as_list(anchors)
    if not anchor_list:
        return text, 0

    start_index = -1
    for anchor in anchor_list:
        start_index = find_anchor_index(text, anchor)
        if start_index >= 0:
            break
    if start_index < 0:
        raise ValueError(f"没有找到正文锚点：{anchor_list}")

    stop_anchor_list = as_list(stop_anchors)
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
