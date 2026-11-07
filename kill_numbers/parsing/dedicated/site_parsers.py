import re

from kill_numbers.parsing.common import (
    CANDIDATE_REGION_WINDOW,
    all_issue_segment_matches,
    extract_issue_numbers,
    find_anchor_index,
    find_last_anchor_index_before,
    find_number_groups,
    filter_candidates_by_region,
    has_duplicate_numbers,
    normalize_region,
    valid_number,
)
from kill_numbers.parsing.diagnostics import detect_available_issues
from kill_numbers.text_utils import (
    html_to_text,
    normalize_issue,
    normalize_keyword,
    unique_keep_order,
)


def _configured_candidate_window(target: dict, label: str) -> int:
    value = target.get("issue_position_window")
    if value is None:
        return CANDIDATE_REGION_WINDOW
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label}候选窗口无效：{value}")
    return value


def _windowed_article_history_section(section: str, target: dict) -> str:
    keyword_list = [
        normalize_keyword(keyword)
        for keyword in (target.get("keywords") or [])
        if keyword
    ]
    expected_count = target.get("count")
    valid_segments: list[str] = []
    for match in all_issue_segment_matches(section):
        segment = match.group(0)
        compact_segment = normalize_keyword(segment)
        if keyword_list and not any(keyword in compact_segment for keyword in keyword_list):
            continue
        groups = [
            group
            for group in find_number_groups(segment)
            if (not expected_count or len(group) == expected_count)
            and all(valid_number(number) for number in group)
            and (
                target.get("allow_duplicate_numbers", False)
                or not has_duplicate_numbers(group)
            )
        ]
        if groups:
            valid_segments.append(segment)

    window = _configured_candidate_window(target, "文章历史")
    region = normalize_region(target.get("region"))
    if region == "top":
        selected = valid_segments[:window]
    elif region == "bottom":
        selected = valid_segments[-window:]
    else:
        raise ValueError("文章历史专属解析需要配置 top 或 bottom")
    return "\n".join(selected)


def _chaoxi_hyphen_top_7_candidates(
    text: str,
    target: dict,
) -> dict[str, list[list[str]]]:
    if normalize_region(target.get("region")) != "top":
        raise ValueError("潮汐文脉专属解析只允许 top")
    if target.get("count") != 7:
        raise ValueError("潮汐文脉专属解析要求 count=7")

    page_text = html_to_text(text)
    anchor = str(target.get("anchor") or "潮汐文脉")
    anchor_index = find_anchor_index(page_text, anchor)
    if anchor_index < 0:
        raise ValueError(f"没有找到正文锚点：{anchor}")

    stop_anchor = target.get("stop_anchor") or "上一篇"
    stop_index = find_anchor_index(
        page_text,
        stop_anchor,
        start=anchor_index + len(anchor),
    )
    section = page_text[anchor_index:stop_index if stop_index >= 0 else len(page_text)]
    matches = all_issue_segment_matches(section)
    if not matches:
        raise ValueError("潮汐文脉专属栏目没有找到期数行")

    header = section[: matches[0].start()]
    keyword_list = [
        normalize_keyword(keyword)
        for keyword in (target.get("keywords") or [])
        if keyword
    ]
    normalized_header = normalize_keyword(header)
    if keyword_list and not any(keyword in normalized_header for keyword in keyword_list):
        raise ValueError("潮汐文脉专属栏目标题缺少目标字段锚点")

    expected_count = target.get("count")
    rows: list[tuple[str, list[str], int]] = []
    for match in matches:
        segment = match.group(0)
        groups = [
            group
            for group in find_number_groups(segment)
            if len(group) == expected_count
            and all(valid_number(number) for number in group)
            and (
                target.get("allow_duplicate_numbers", False)
                or not has_duplicate_numbers(group)
            )
        ]
        if len(groups) > 1:
            preview = " | ".join(",".join(group) for group in groups[:5])
            raise ValueError(
                f"{normalize_issue(match.group(1))}期候选不唯一，已停止输出避免抓错：{preview}"
            )
        if groups:
            rows.append((normalize_issue(match.group(1)), groups[0], match.start()))

    if not rows:
        raise ValueError("潮汐文脉专属栏目没有有效七码历史行")

    window = _configured_candidate_window(target, "潮汐文脉专属栏目")
    selected = rows[:window]
    candidates: dict[str, list[list[str]]] = {}
    for issue, numbers, _position in selected:
        candidates.setdefault(issue, []).append(numbers)
    return candidates


def chaoxi_hyphen_top_7_available_issues(
    text: str,
    target: dict,
) -> list[str]:
    return list(_chaoxi_hyphen_top_7_candidates(text, target))


def extract_chaoxi_hyphen_top_7_numbers(
    text: str,
    issues: list[str],
    target: dict,
) -> dict[str, list[str]]:
    candidates = _chaoxi_hyphen_top_7_candidates(text, target)
    found: dict[str, list[str]] = {}
    for issue in (normalize_issue(item) for item in issues):
        distinct: list[list[str]] = []
        for numbers in candidates.get(issue, []):
            if numbers not in distinct:
                distinct.append(numbers)
        if len(distinct) > 1:
            preview = " | ".join(",".join(numbers) for numbers in distinct[:5])
            raise ValueError(f"{issue}期候选不唯一，已停止输出避免抓错：{preview}")
        if distinct:
            found[issue] = distinct[0]
    return found


def extract_discuss_detail_top_10_numbers(
    text: str,
    issues: list[str],
    target: dict,
) -> dict[str, list[str]]:
    if normalize_region(target.get("region")) != "top":
        raise ValueError("讨论文章专属解析只允许 top")
    if target.get("count") != 10:
        raise ValueError("讨论文章专属解析要求 count=10")
    return extract_issue_numbers(
        text,
        issues,
        keywords=target.get("keywords"),
        expected_count=target.get("count"),
        strict_ambiguous=True,
        allow_duplicate_numbers=target.get("allow_duplicate_numbers", False),
        anchor=target.get("anchor"),
        stop_anchor=target.get("stop_anchor"),
        region="top",
        issue_position_window=target.get("issue_position_window"),
    )


def _huaxianzi_bottom_7_candidates(
    text: str,
    target: dict,
) -> dict[str, list[list[str]]]:
    if normalize_region(target.get("region")) != "bottom":
        raise ValueError("花仙子绝杀七码专属解析只允许 bottom")
    if target.get("count") != 7:
        raise ValueError("花仙子绝杀七码专属解析要求 count=7")

    page_text = html_to_text(text)
    anchor = str(target.get("anchor") or "")
    anchor_positions = [
        match.start() for match in re.finditer(re.escape(anchor), page_text)
    ]
    if len(anchor_positions) != 1:
        raise ValueError(f"花仙子绝杀七码栏目锚点数量异常：{len(anchor_positions)}")

    start = anchor_positions[0]
    stop_anchor = str(target.get("stop_anchor") or "点击投注")
    stop = find_anchor_index(page_text, stop_anchor, start=start + len(anchor))
    if stop < 0:
        raise ValueError("花仙子绝杀七码栏目缺少停止边界：点击投注")
    section = page_text[start:stop]
    keyword_list = [
        normalize_keyword(keyword)
        for keyword in (target.get("keywords") or [])
        if keyword
    ]
    rows: list[tuple[str, list[str], int]] = []
    for match in all_issue_segment_matches(section):
        segment = match.group(0)
        compact_segment = normalize_keyword(segment)
        if keyword_list and not any(keyword in compact_segment for keyword in keyword_list):
            continue
        groups = [
            group
            for group in find_number_groups(segment)
            if len(group) == 7
            and all(valid_number(number) for number in group)
            and (
                target.get("allow_duplicate_numbers", False)
                or not has_duplicate_numbers(group)
            )
        ]
        if len(groups) > 1:
            preview = " | ".join(",".join(group) for group in groups[:5])
            raise ValueError(
                f"{normalize_issue(match.group(1))}期候选不唯一，已停止输出避免抓错：{preview}"
            )
        if groups:
            rows.append((normalize_issue(match.group(1)), groups[0], match.start()))

    if not rows:
        raise ValueError("花仙子绝杀七码栏目没有有效历史行")

    window = _configured_candidate_window(target, "花仙子绝杀七码栏目")
    candidates: dict[str, list[list[str]]] = {}
    for issue, numbers, _position in rows[-window:]:
        candidates.setdefault(issue, []).append(numbers)
    return candidates


def huaxianzi_bottom_7_available_issues(
    text: str,
    target: dict,
) -> list[str]:
    return list(_huaxianzi_bottom_7_candidates(text, target))


def extract_huaxianzi_bottom_7_numbers(
    text: str,
    issues: list[str],
    target: dict,
) -> dict[str, list[str]]:
    candidates = _huaxianzi_bottom_7_candidates(text, target)
    found: dict[str, list[str]] = {}
    for issue in (normalize_issue(item) for item in issues):
        distinct: list[list[str]] = []
        for numbers in candidates.get(issue, []):
            if numbers not in distinct:
                distinct.append(numbers)
        if len(distinct) > 1:
            preview = " | ".join(",".join(numbers) for numbers in distinct[:5])
            raise ValueError(f"{issue}期候选不唯一，已停止输出避免抓错：{preview}")
        if distinct:
            found[issue] = distinct[0]
    return found


def extract_macau_baoma_numbers(
    text: str,
    issues: list[str],
    expected_count: int | None = None,
    region: str | None = None,
) -> dict[str, list[str]]:
    issue_set = {normalize_issue(issue) for issue in issues}
    rows: list[tuple[list[str], str, int]] = []
    pattern = re.compile(
        r"<span[^>]*class=['\"]am_qi['\"][^>]*>\s*0?(\d{1,3})\s*期\s*</span>\s*"
        r"<span[^>]*class=['\"]am_yc['\"][^>]*>(.*?)</span>",
        re.I | re.S,
    )
    for match in pattern.finditer(text):
        issue = normalize_issue(match.group(1))
        numbers = re.findall(r"\d+", html_to_text(match.group(2)))
        if any(len(number) != 2 or not valid_number(number) for number in numbers):
            continue
        if expected_count and len(numbers) != expected_count:
            continue
        rows.append((numbers, issue, match.start()))

    selected_rows = filter_candidates_by_region(
        [(numbers, issue, position) for numbers, issue, position in rows],
        region,
        strict_window=True,
        require_region=True,
    )
    candidates: dict[str, list[list[str]]] = {}
    for numbers, issue, _position in selected_rows:
        if issue not in issue_set:
            continue
        issue_candidates = candidates.setdefault(issue, [])
        if numbers not in issue_candidates:
            issue_candidates.append(numbers)

    found: dict[str, list[str]] = {}
    for issue, issue_candidates in candidates.items():
        if len(issue_candidates) > 1:
            preview = " | ".join(",".join(numbers) for numbers in issue_candidates[:5])
            raise ValueError(f"{issue}期 候选不唯一，已停止输出避免抓错：{preview}")
        found[issue] = issue_candidates[0]
    return found


def extract_zuibaxian_top7_numbers(
    text: str,
    issues: list[str],
    expected_count: int | None = 10,
) -> dict[str, list[str]]:
    section_pattern = re.compile(r"<div\b[^>]*\bid=['\"]top_7['\"][^>]*>", re.I)
    matches = list(section_pattern.finditer(text))
    if len(matches) != 1:
        raise ValueError(f"醉八仙 top_7 栏目数量异常：{len(matches)}")

    start = matches[0].end()
    next_section = re.search(r"<div\b[^>]*\bid=['\"]top_\d+['\"][^>]*>", text[start:], re.I)
    end = start + next_section.start() if next_section else len(text)
    section = text[start:end]
    return extract_issue_numbers(
        section,
        issues,
        keywords=["不买十码"],
        expected_count=expected_count,
        strict_ambiguous=True,
        region="top",
        issue_position_window=CANDIDATE_REGION_WINDOW,
    )


def extract_white_tiger_stable_10_numbers(
    text: str,
    issues: list[str],
    expected_count: int | None = 10,
) -> dict[str, list[str]]:
    page_text = html_to_text(text)
    heading = re.search(r"(?<!\d)\d{1,3}\s*期\s*稳杀10码", page_text)
    if not heading:
        raise ValueError("白虎玄机没有找到当前期稳杀10码栏目标题")

    end = find_anchor_index(page_text, "上一篇", start=heading.end())
    if end < 0:
        raise ValueError("白虎玄机没有找到栏目停止边界：上一篇")
    section = page_text[heading.start():end]
    return extract_issue_numbers(
        section,
        issues,
        keywords=["绝杀十码"],
        expected_count=expected_count,
        strict_ambiguous=True,
        region="top",
        issue_position_window=CANDIDATE_REGION_WINDOW,
    )


def extract_top_article_history_numbers(
    text: str,
    issues: list[str],
    target: dict,
) -> dict[str, list[str]]:
    if normalize_region(target.get("region")) not in {"top", "bottom"}:
        raise ValueError("当前文章标题专属解析需要配置 top 或 bottom")

    page_text = html_to_text(text)
    author_index = find_anchor_index(page_text, target.get("anchor") or "")
    title_index = find_last_anchor_index_before(
        page_text,
        target.get("article_title_anchor") or "",
        author_index,
    )
    if author_index < 0 or title_index < 0 or author_index - title_index > 600:
        raise ValueError("没有找到当前文章标题与作者的专属边界")

    stop_anchor = target.get("stop_anchor")
    stop_index = (
        find_anchor_index(page_text, stop_anchor, start=author_index + 1)
        if stop_anchor
        else -1
    )
    section = page_text[author_index:stop_index if stop_index >= 0 else len(page_text)]
    candidate_section = _windowed_article_history_section(section, target)
    return extract_issue_numbers(
        candidate_section,
        issues,
        keywords=target.get("keywords"),
        expected_count=target.get("count"),
        strict_ambiguous=True,
        region=None,
    )


def extract_top_article_history_current_cycle_numbers(
    text: str,
    issues: list[str],
    target: dict,
) -> dict[str, list[str]]:
    """Extract current-cycle rows with or without an embedded legacy cycle."""
    region = normalize_region(target.get("region"))
    if region not in {"top", "bottom"}:
        raise ValueError("当前周期专属解析需要配置 top 或 bottom")

    page_text = html_to_text(text)
    author_index = find_anchor_index(page_text, target.get("anchor") or "")
    title_index = find_last_anchor_index_before(
        page_text,
        target.get("article_title_anchor") or "",
        author_index,
    )
    if author_index < 0 or title_index < 0 or author_index - title_index > 600:
        raise ValueError("没有找到当前文章标题与作者的专属边界")

    stop_anchor = target.get("stop_anchor")
    stop_index = (
        find_anchor_index(page_text, stop_anchor, start=author_index + 1)
        if stop_anchor
        else -1
    )
    if stop_index < 0:
        raise ValueError("没有找到正文停止边界：上一篇")

    title_text = page_text[title_index:author_index]
    title_issues = [
        normalize_issue(match.group(1))
        for match in re.finditer(r"(?<!\d)0?(\d{1,3})\s*期", title_text)
    ]
    if len(title_issues) != 1:
        raise ValueError(f"当前文章标题期数异常：{title_issues}")
    current_issue = title_issues[0]

    section = page_text[author_index:stop_index]
    keyword_list = [
        normalize_keyword(keyword)
        for keyword in (target.get("keywords") or [])
        if keyword
    ]
    expected_count = target.get("count")
    rows: list[tuple[str, list[str], int]] = []
    for match in all_issue_segment_matches(section):
        segment = match.group(0)
        compact_segment = normalize_keyword(segment)
        if keyword_list and not any(keyword in compact_segment for keyword in keyword_list):
            continue
        groups = [
            group
            for group in find_number_groups(segment)
            if (not expected_count or len(group) == expected_count)
            and all(valid_number(number) for number in group)
            and (
                target.get("allow_duplicate_numbers", False)
                or not has_duplicate_numbers(group)
            )
        ]
        for group in groups:
            rows.append((normalize_issue(match.group(1)), group, match.start()))

    if not rows:
        raise ValueError("专属栏目没有有效号码行")

    # The page has no DOM marker between the current and legacy cycles.
    cycle_seams = [
        index
        for index in range(1, len(rows))
        if rows[index - 1][0] == "365" and rows[index][0] == "1"
    ]
    if len(cycle_seams) > 1:
        raise ValueError(f"周期文档边界数量异常：{len(cycle_seams)}")

    if cycle_seams:
        seam = cycle_seams[0]
        blocks = [rows[:seam], rows[seam:]]
    else:
        blocks = [rows]
    title_window = _configured_candidate_window(target, "当前周期")
    primary_block_index = 0 if region == "top" else len(blocks) - 1
    primary_block = blocks[primary_block_index]
    directional_blocks = [
        block[:title_window] if region == "top" else block[-title_window:]
        for block in blocks
    ]
    title_positions = [
        index
        for index, (issue, _numbers, _position) in enumerate(primary_block)
        if issue == current_issue
    ]
    if not title_positions or current_issue not in {
        issue for issue, _numbers, _position in directional_blocks[primary_block_index]
    }:
        direction_label = "顶部" if region == "top" else "底部"
        raise ValueError(
            f"当前文章标题期数 {current_issue} 不在{direction_label}当前周期窗口内（{title_window}条）"
        )

    found: dict[str, list[str]] = {}
    for requested_issue in (normalize_issue(issue) for issue in issues):
        selected_candidates = [
            numbers
            for issue, numbers, _position in directional_blocks[primary_block_index]
            if issue == requested_issue
        ]
        if not selected_candidates:
            continue

        distinct: list[list[str]] = []
        for numbers in selected_candidates:
            if numbers not in distinct:
                distinct.append(numbers)
        if len(distinct) > 1:
            preview = " | ".join(",".join(numbers) for numbers in distinct[:5])
            raise ValueError(
                f"{requested_issue}期 候选不唯一，已停止输出避免抓错：{preview}"
            )
        found[requested_issue] = distinct[0]

    return found


def huxin_xiaozhu_stable_10_section(text: str, target: dict) -> str:
    if normalize_region(target.get("region")) != "top":
        raise ValueError("湖心小筑绝杀十码专属解析只允许 top")
    if target.get("count") != 10:
        raise ValueError("湖心小筑绝杀十码专属解析要求 count 为 10")

    page_text = html_to_text(text)
    anchor = target.get("anchor") or ""
    if not anchor or find_anchor_index(page_text, anchor) < 0:
        raise ValueError("湖心小筑没有找到正文锚点")

    header = re.compile(r"湖心小筑\s*-\s*绝杀十码\s*-", re.I)
    matches = list(header.finditer(page_text))
    if len(matches) != 1:
        raise ValueError(f"湖心小筑绝杀十码专属栏目数量异常：{len(matches)}")

    start = matches[0].end()
    next_section = re.search(r"\n\s*湖心小筑\s*\n\s*-", page_text[start:])
    if not next_section:
        raise ValueError("湖心小筑绝杀十码没有找到栏目停止边界")
    return page_text[start:start + next_section.start()]


def huxin_xiaozhu_stable_10_available_issues(text: str, target: dict) -> list[str]:
    section = huxin_xiaozhu_stable_10_section(text, target)
    return detect_available_issues(
        section,
        keywords=["绝杀帝绝杀10码"],
        expected_count=10,
        region="top",
        issue_position_window=CANDIDATE_REGION_WINDOW,
    )


def extract_huxin_xiaozhu_stable_10_numbers(
    text: str,
    issues: list[str],
    target: dict,
) -> dict[str, list[str]]:
    section = huxin_xiaozhu_stable_10_section(text, target)
    return extract_issue_numbers(
        section,
        issues,
        keywords=["绝杀帝绝杀10码"],
        expected_count=10,
        strict_ambiguous=True,
        region="top",
        issue_position_window=CANDIDATE_REGION_WINDOW,
    )


YIRAN_GONGZHU_PART_ANCHORS = (
    "依然公主尽位杀码",
    "依然公主首位杀码",
)


def _yiran_gongzhu_bottom_6_candidates(
    text: str,
    target: dict,
    *,
    evidence: list[tuple[str, list[str], str, int, int, int]] | None = None,
) -> dict[str, list[list[str]]]:
    """Merge the two named 3-number columns from one article."""
    if normalize_region(target.get("region")) != "bottom":
        raise ValueError("依然公主专属解析只允许 bottom")
    if target.get("count") != 6:
        raise ValueError("依然公主专属解析要求 count=6")
    if tuple(target.get("part_anchors") or ()) != YIRAN_GONGZHU_PART_ANCHORS:
        raise ValueError("依然公主专属解析必须配置两个固定区块锚点")

    page_text = html_to_text(text)
    window = _configured_candidate_window(target, "依然公主专属栏目")
    rows_by_part: dict[str, list[tuple[str, list[str], int, int]]] = {}
    for part_anchor in YIRAN_GONGZHU_PART_ANCHORS:
        rows: list[tuple[str, list[str], int, int]] = []
        for match in all_issue_segment_matches(page_text):
            segment = match.group(0)
            if normalize_keyword(part_anchor) not in normalize_keyword(segment):
                continue
            groups = [
                group
                for group in find_number_groups(segment)
                if len(group) == 3
                and all(valid_number(number) for number in group)
            ]
            if len(groups) > 1:
                raise ValueError(
                    f"{normalize_issue(match.group(1))}期 {part_anchor}候选不唯一"
                )
            if groups:
                rows.append(
                    (
                        normalize_issue(match.group(1)),
                        groups[0],
                        match.start(),
                        match.end(),
                    )
                )
        if not rows:
            raise ValueError(f"依然公主缺少区块：{part_anchor}")
        rows_by_part[part_anchor] = rows[-window:]

    candidates: dict[str, list[list[str]]] = {}
    by_issue = [
        {
            issue: (numbers, start, end)
            for issue, numbers, start, end in rows
        }
        for rows in rows_by_part.values()
    ]
    for issue in by_issue[0]:
        if issue not in by_issue[1]:
            continue
        first = by_issue[0][issue]
        second = by_issue[1][issue]
        numbers = first[0] + second[0]
        if len(numbers) != 6 or has_duplicate_numbers(numbers):
            raise ValueError(f"{issue}期合并号码有重复或数量错误")
        candidates.setdefault(issue, []).append(numbers)
        if evidence is not None:
            evidence.append(
                (
                    issue,
                    numbers,
                    " + ".join(YIRAN_GONGZHU_PART_ANCHORS),
                    min(first[1], second[1]),
                    max(first[2], second[2]),
                    min(first[1], second[1]),
                )
            )
    return candidates


def yiran_gongzhu_bottom_6_available_issues(text: str, target: dict) -> list[str]:
    return list(_yiran_gongzhu_bottom_6_candidates(text, target))


def extract_yiran_gongzhu_bottom_6_numbers(
    text: str,
    issues: list[str],
    target: dict,
) -> dict[str, list[str]]:
    candidates = _yiran_gongzhu_bottom_6_candidates(text, target)
    found: dict[str, list[str]] = {}
    for issue in (normalize_issue(item) for item in issues):
        distinct = []
        for numbers in candidates.get(issue, []):
            if numbers not in distinct:
                distinct.append(numbers)
        if len(distinct) > 1:
            raise ValueError(f"{issue}期依然公主候选不唯一")
        if distinct:
            found[issue] = distinct[0]
    return found


def xinzhu_forum_top_candidates(text: str, target: dict, *, evidence=None) -> dict[str, list[list[str]]]:
    if normalize_region(target.get("region")) != "top":
        raise ValueError("新竹论坛绝杀十码专属解析只允许 top")
    if target.get("count") != 10:
        raise ValueError("新竹论坛绝杀十码专属解析要求 count 为 10")

    page_text = html_to_text(text)
    heading_pattern = re.compile(
        r"精英榜\s*0?\d{1,3}\s*期\s*[:：]\s*[\[【]\s*绝杀十码\s*[\]】]\s*已公开"
    )
    row_pattern = re.compile(
        r"(?m)^\s*0?(\d{1,3})\s*期\s*[:：]\s*绝杀十码\s*[:：]\s*"
        r"((?:\d{2}\s*[.,、，]\s*){9}\d{2})(?=\s*[:：])"
    )
    headings = list(heading_pattern.finditer(page_text))
    if not headings:
        raise ValueError("新竹论坛没有找到精英榜绝杀十码栏目标题")

    window = _configured_candidate_window(target, "新竹论坛顶部")

    candidates: dict[str, list[list[str]]] = {}
    for heading in headings:
        stop = re.search(r"(?m)^\s*(?:上|下)一篇\s*[:：]", page_text[heading.end():])
        if not stop:
            raise ValueError("新竹论坛没有找到栏目停止边界：上一篇")
        section = page_text[heading.end():heading.end() + stop.start()]
        for row in list(row_pattern.finditer(section))[:window]:
            issue = normalize_issue(row.group(1))
            numbers = re.findall(r"\d{2}", row.group(2))
            if len(numbers) != 10 or any(not valid_number(number) for number in numbers):
                continue
            if has_duplicate_numbers(numbers):
                raise ValueError(f"{issue}期 号码有重复，已停止输出避免抓错")
            candidates.setdefault(issue, []).append(numbers)
            if evidence is not None:
                evidence.append((issue, numbers, heading.group(), heading.end(),
                                 heading.end() + stop.start(), heading.end() + row.start()))
    return candidates


def extract_xinzhu_forum_stable_10_numbers(
    text: str,
    issues: list[str],
    target: dict,
) -> dict[str, list[str]]:
    candidates = xinzhu_forum_top_candidates(text, target)

    found: dict[str, list[str]] = {}
    for issue in (normalize_issue(item) for item in issues):
        distinct: list[list[str]] = []
        for numbers in candidates.get(issue, []):
            if numbers not in distinct:
                distinct.append(numbers)
        if len(distinct) > 1:
            preview = " | ".join(",".join(numbers) for numbers in distinct[:5])
            raise ValueError(f"{issue}期 候选不唯一，已停止输出避免抓错：{preview}")
        if distinct:
            found[issue] = distinct[0]
    return found


def _liuhe_bottom_10_candidates(
    text: str,
    target: dict,
    *, evidence=None,
) -> dict[str, list[list[str]]]:
    if normalize_region(target.get("region")) != "bottom":
        raise ValueError("六合绝杀十码专属解析只允许 bottom")
    if target.get("count") != 10:
        raise ValueError("六合绝杀十码专属解析要求 count 为 10")

    page_text = html_to_text(text)
    heading_pattern = re.compile(
        r"(?m)^\s*0?\d{1,3}\s*期\s*绝杀\s*(?:十|10)\s*码\s*$"
    )
    headings = list(heading_pattern.finditer(page_text))
    if len(headings) != 1:
        raise ValueError(f"六合绝杀十码数据标题数量异常：{len(headings)}")

    row_pattern = re.compile(
        r"(?m)^\s*0?(\d{1,3})\s*期\s*[:：]\s*"
        r"(?:⚔️\s*)?绝杀\s*(?:十|10)\s*码\s*(?:⚔️\s*)?"
        r"[\[【]\s*((?:\d{2}\s*[.,，。、]\s*){9}\d{2})\s*[\]】]"
    )
    rows: list[tuple[str, list[str], int]] = []
    for row in row_pattern.finditer(page_text[headings[0].end():]):
        numbers = re.findall(r"\d{2}", row.group(2))
        if len(numbers) != 10 or any(not valid_number(number) for number in numbers):
            continue
        if has_duplicate_numbers(numbers):
            continue
        rows.append((normalize_issue(row.group(1)), numbers, headings[0].end() + row.start()))

    window = _configured_candidate_window(target, "六合绝杀十码尾部")
    candidates: dict[str, list[list[str]]] = {}
    for issue, numbers, offset in rows[-window:]:
        candidates.setdefault(issue, []).append(numbers)
        if evidence is not None:
            evidence.append((issue, numbers, headings[0].group(), headings[0].end(), len(page_text), offset))

    return candidates


def extract_liuhe_bottom_10_numbers(
    text: str,
    issues: list[str],
    target: dict,
) -> dict[str, list[str]]:
    candidates = _liuhe_bottom_10_candidates(text, target)
    found: dict[str, list[str]] = {}
    for issue in (normalize_issue(item) for item in issues):
        distinct: list[list[str]] = []
        for numbers in candidates.get(issue, []):
            if numbers not in distinct:
                distinct.append(numbers)
        if len(distinct) > 1:
            preview = " | ".join(",".join(numbers) for numbers in distinct[:5])
            raise ValueError(f"{issue}期 候选不唯一，已停止输出避免抓错：{preview}")
        if distinct:
            found[issue] = distinct[0]
    return found


def liuhe_bottom_10_available_issues(text: str, target: dict) -> list[str]:
    return list(_liuhe_bottom_10_candidates(text, target))


DEDICATED_TEN_ROW_CONTRACTS = {
    "shita_top_10": {
        "label": "师太",
        "region": "top",
        "heading": re.compile(
            r"(?m)^\s*大家发\s*[\(（]\s*绝杀\s*10\s*码\s*[\)）]\s*$"
        ),
        "stop": re.compile(
            r"(?m)^\s*(?:"
            r"最早发表在[^\r\n]*欢迎转发\+关注"
            r"|document\.writeln"
            r"|大家发\s*[\(（](?!\s*绝杀\s*10\s*码)"
            r")"
        ),
        "row": re.compile(
            r"(?<!\d)0?(\d{1,3})\s*期\s*[:：]\s*[『「【\[]\s*师太\s*[』」】\]]\s*"
            r"绝杀\s*(?:十|10)\s*码\s*开\s*[:：][^\[【]{0,30}[\[【]\s*"
            r"((?:\d{2}\s*[.,，。、]\s*){9}\d{2})\s*[\]】]",
            re.M,
        ),
    },
    "shanshui_xiangfeng_top_10": {
        "label": "山水相逢",
        "region": "top",
        "heading": re.compile(
            r"(?<!\d)\d{1,3}\s*期\s*[:：]\s*金马王\s*[\[【]\s*绝杀十码\s*[\]】]\s*山水相逢"
        ),
        "stop": re.compile(r"上一篇\s*[:：]"),
        "row": re.compile(
            r"(?<!\d)0?(\d{1,3})\s*期\s*[:：]\s*拳王杀料\s*绝杀\s*10\s*码\s*"
            r"[\[【]\s*((?:\d{2}\s*[.,，。、]\s*){9}\d{2})\s*[\]】]\s*开\s*[:：]"
        ),
    },
    "majing_forum_bottom_10": {
        "label": "马经论坛",
        "region": "bottom",
        "heading": re.compile(r"澳门马经论坛\s*[\[【]\s*绝杀十码\s*[\]】]"),
        "stop": re.compile(r"澳门马经论坛\s*[\[【]\s*内部六尾\s*[\]】]"),
        "row": re.compile(
            r"(?<!\d)0?(\d{1,3})\s*期\s*绝杀十码\s*[:：]\s*"
            r"((?:\d{2}\s*[.,，。、]\s*){9}\d{2})\s*开\s*[:：]"
        ),
    },
    "chunyin_qiushe_bottom_10": {
        "label": "春蚓秋蛇",
        "region": "bottom",
        "heading": re.compile(
            r"(?<!\d)\d{1,3}\s*期\s*[:：]\s*春蚓秋蛇[^\n]{0,20}"
            r"[\[【]\s*绝杀10码\s*[\]】][^\n]{0,20}继续公开"
        ),
        "stop": re.compile(r"下一贴\s*[:：]"),
        "row": re.compile(
            r"(?<!\d)0?(\d{1,3})\s*期\s*[:：]\s*[\[【]\s*春蚓秋蛇\s*[\]】]"
            r"[^\n]{0,30}绝杀10码[^\n]{0,30}开\s*[:：][^\n]{0,30}\s*"
            r"[\[【]\s*((?:\d{2}\s*[.,，。、]\s*){9}\d{2})\s*[\]】]",
            re.M,
        ),
    },
    "shizhiminggui_bottom_10": {
        "label": "实至名归",
        "region": "bottom",
        "heading": re.compile(
            r"(?m)^\s*0?\d{1,3}\s*期\s*[:：]\s*实至名归\s*"
            r"[\[【]\s*绝杀\s*(?:十|10)\s*码\s*[\]】]\s*共同致富\s*$"
        ),
        "stop": re.compile(r"(?m)^\s*下一贴\s*[:：]"),
        "row": re.compile(
            r"(?m)^\s*0?(\d{1,3})\s*期\s*[:：][^\r\n]{0,100}"
            r"实至名归[^\r\n]{0,100}绝杀\s*(?:十|10)\s*码[^\r\n]*\r?\n"
            r"\s*[\[【]\s*((?:\d{2}\s*[.,，。、]\s*){9}\d{2})\s*[\]】]"
        ),
    },
}


def dedicated_ten_row_candidates(text: str, target: dict, parser_name: str, *, evidence=None) -> dict[str, list[list[str]]]:
    contract = DEDICATED_TEN_ROW_CONTRACTS[parser_name]
    label = contract["label"]
    expected_region = contract["region"]
    if normalize_region(target.get("region")) != expected_region:
        raise ValueError(f"{label}专属解析只允许 {expected_region}")
    if target.get("count") != 10:
        raise ValueError(f"{label}专属解析要求 count 为 10")

    page_text = html_to_text(text)
    headings = list(contract["heading"].finditer(page_text))
    if len(headings) != 1:
        raise ValueError(f"{label}专属栏目标题数量异常：{len(headings)}")
    stop = contract["stop"].search(page_text, headings[0].end())
    if not stop:
        if parser_name == "shita_top_10":
            stop = re.search(r"\Z", page_text)
        else:
            raise ValueError(f"{label}专属栏目没有找到停止边界")
    if stop is None:
        raise ValueError(f"{label}专属栏目没有找到停止边界")
    section = page_text[headings[0].start():stop.start()]

    window = _configured_candidate_window(target, f"{label}")

    rows = list(contract["row"].finditer(section))
    if parser_name == "shita_top_10" and rows:
        content_start = headings[0].end() - headings[0].start()
        prefix = section[content_start:rows[0].start()]
        if len([line for line in prefix.splitlines() if line.strip()]) > 1:
            raise ValueError("师太专属标题与首条数据之间出现未知内容")
        for previous, current in zip(rows, rows[1:]):
            if section[previous.end():current.start()].strip():
                raise ValueError("师太专属数据行之间出现未知内容")
        if section[rows[-1].end():].strip():
            raise ValueError("师太专属末条数据后出现未知内容")
    rows = rows[:window] if expected_region == "top" else rows[-window:]
    candidates: dict[str, list[list[str]]] = {}
    for row in rows:
        issue = normalize_issue(row.group(1))
        numbers = re.findall(r"\d{2}", row.group(2))
        if len(numbers) != 10 or any(not valid_number(number) for number in numbers):
            continue
        if has_duplicate_numbers(numbers):
            raise ValueError(f"{issue}期 号码有重复，已停止输出避免抓错")
        candidates.setdefault(issue, []).append(numbers)
        if evidence is not None:
            evidence.append((issue, numbers, headings[0].group(), headings[0].start(),
                             stop.start(), headings[0].start() + row.start()))
    return candidates


def dedicated_candidate_location(text: str, target: dict, issue: str, numbers: list[str]):
    """Return offsets from the same bounded rows used by the dedicated parser."""
    parser = target.get('special_parser')
    evidence = []
    if parser == 'yiran_gongzhu_bottom_6':
        _yiran_gongzhu_bottom_6_candidates(text, target, evidence=evidence)
    elif parser == 'liuhe_bottom_10':
        _liuhe_bottom_10_candidates(text, target, evidence=evidence)
    elif parser == 'xinzhu_forum_stable_10':
        xinzhu_forum_top_candidates(text, target, evidence=evidence)
    elif parser in DEDICATED_TEN_ROW_CONTRACTS:
        dedicated_ten_row_candidates(text, target, parser, evidence=evidence)
    else:
        return None
    matches = [row[2:] for row in evidence if row[0] == issue and row[1] == numbers]
    if not matches:
        raise ValueError(f'{issue}期专属候选缺少同块偏移证据')
    return matches[-1] if normalize_region(target.get('region')) == 'bottom' else matches[0]


def dedicated_ten_available_issues(text: str, target: dict, parser_name: str) -> list[str]:
    return list(dedicated_ten_row_candidates(text, target, parser_name))


def extract_dedicated_ten_numbers(
    text: str,
    issues: list[str],
    target: dict,
    parser_name: str,
) -> dict[str, list[str]]:
    candidates = dedicated_ten_row_candidates(text, target, parser_name)
    found: dict[str, list[str]] = {}
    for issue in (normalize_issue(item) for item in issues):
        distinct: list[list[str]] = []
        for numbers in candidates.get(issue, []):
            if numbers not in distinct:
                distinct.append(numbers)
        if len(distinct) > 1:
            preview = " | ".join(",".join(numbers) for numbers in distinct[:5])
            raise ValueError(f"{issue}期 候选不唯一，已停止输出避免抓错：{preview}")
        if distinct:
            found[issue] = distinct[0]
    return found


def extract_shanshui_xiangfeng_top_10_numbers(text: str, issues: list[str], target: dict):
    return extract_dedicated_ten_numbers(text, issues, target, "shanshui_xiangfeng_top_10")


def extract_majing_forum_bottom_10_numbers(text: str, issues: list[str], target: dict):
    return extract_dedicated_ten_numbers(text, issues, target, "majing_forum_bottom_10")


def extract_chunyin_qiushe_bottom_10_numbers(text: str, issues: list[str], target: dict):
    return extract_dedicated_ten_numbers(text, issues, target, "chunyin_qiushe_bottom_10")


def fengwu_jiutian_bottom_10_section(text: str, target: dict) -> str:
    if normalize_region(target.get("region")) != "bottom":
        raise ValueError("凤舞九天专属解析只允许 bottom")
    if target.get("count") != 10:
        raise ValueError("凤舞九天专属解析要求 count=10")

    page_text = html_to_text(text)

    def exact_heading_offsets(anchor: str) -> list[int]:
        normalized_anchor = normalize_keyword(anchor)
        offsets: list[int] = []
        offset = 0
        for line in page_text.splitlines(keepends=True):
            if normalize_keyword(line.strip()) == normalized_anchor:
                offsets.append(offset)
            offset += len(line)
        return offsets

    start_offsets = exact_heading_offsets(str(target.get("anchor") or ""))
    stop_offsets = exact_heading_offsets(str(target.get("stop_anchor") or ""))
    if len(start_offsets) != 1:
        raise ValueError(f"凤舞九天专属栏目起点锚点数量异常：{len(start_offsets)}")
    if len(stop_offsets) != 1:
        raise ValueError(f"凤舞九天专属栏目终点锚点数量异常：{len(stop_offsets)}")
    if stop_offsets[0] <= start_offsets[0]:
        raise ValueError("凤舞九天专属栏目边界顺序异常")
    return page_text[start_offsets[0]:stop_offsets[0]]


def fengwu_jiutian_bottom_10_available_issues(text: str, target: dict) -> list[str]:
    section = fengwu_jiutian_bottom_10_section(text, target)
    return detect_available_issues(
        section,
        keywords=target.get("keywords"),
        expected_count=10,
        region="bottom",
        issue_position_window=3,
    )


def extract_fengwu_jiutian_bottom_10_numbers(
    text: str,
    issues: list[str],
    target: dict,
) -> dict[str, list[str]]:
    section = fengwu_jiutian_bottom_10_section(text, target)
    return extract_issue_numbers(
        section,
        issues,
        keywords=target.get("keywords"),
        expected_count=10,
        strict_ambiguous=True,
        allow_duplicate_numbers=False,
        region="bottom",
        issue_position_window=3,
    )


def qiancai_liangde_bottom_10_candidates(
    text: str,
    target: dict,
) -> dict[str, list[list[str]]]:
    if normalize_region(target.get("region")) != "bottom":
        raise ValueError("钱彩两得专属解析只允许 bottom")
    if target.get("count") != 10:
        raise ValueError("钱彩两得专属解析要求 count=10")

    identity = str(target.get("article_identity") or "")
    page_text = html_to_text(text)

    def exact_heading_offsets(anchor: str) -> list[int]:
        normalized_anchor = normalize_keyword(anchor)
        offsets: list[int] = []
        offset = 0
        for line in page_text.splitlines(keepends=True):
            if normalize_keyword(line.strip()) == normalized_anchor:
                offsets.append(offset)
            offset += len(line)
        return offsets

    start_offsets = exact_heading_offsets(str(target.get("anchor") or ""))
    stop_offsets = exact_heading_offsets(str(target.get("stop_anchor") or ""))
    if len(start_offsets) != 1:
        raise ValueError(f"钱彩两得专属栏目起点锚点数量异常：{len(start_offsets)}")
    if len(stop_offsets) != 1:
        raise ValueError(f"钱彩两得专属栏目终点锚点数量异常：{len(stop_offsets)}")
    if stop_offsets[0] <= start_offsets[0]:
        raise ValueError("钱彩两得专属栏目边界顺序异常")

    section = page_text[start_offsets[0]:stop_offsets[0]]
    normalized_identity = normalize_keyword(identity)
    rows: list[tuple[str, list[str], int]] = []
    for match in all_issue_segment_matches(section):
        segment = match.group(0)
        compact = normalize_keyword(segment)
        identity_position = compact.find(normalized_identity)
        if identity_position < 0 or identity_position > 40 or "绝杀10码" not in compact:
            continue
        issue = normalize_issue(match.group(1))
        for numbers in find_number_groups(segment):
            if len(numbers) != 10:
                continue
            if has_duplicate_numbers(numbers):
                raise ValueError(f"{issue}期 号码有重复，已停止输出避免抓错")
            rows.append((issue, numbers, match.start()))

    candidates: dict[str, list[list[str]]] = {}
    for issue, numbers, _position in rows[-CANDIDATE_REGION_WINDOW:]:
        candidates.setdefault(issue, []).append(numbers)
    return candidates


def qiancai_liangde_bottom_10_available_issues(text: str, target: dict) -> list[str]:
    return list(qiancai_liangde_bottom_10_candidates(text, target))


def extract_qiancai_liangde_bottom_10_numbers(
    text: str,
    issues: list[str],
    target: dict,
) -> dict[str, list[str]]:
    candidates = qiancai_liangde_bottom_10_candidates(text, target)
    found: dict[str, list[str]] = {}
    for issue in (normalize_issue(item) for item in issues):
        distinct: list[list[str]] = []
        for numbers in candidates.get(issue, []):
            if numbers not in distinct:
                distinct.append(numbers)
        if len(distinct) > 1:
            preview = " | ".join(",".join(numbers) for numbers in distinct[:5])
            raise ValueError(f"{issue}期 候选不唯一，已停止输出避免抓错：{preview}")
        if distinct:
            found[issue] = distinct[0]
    return found


IDENTITY_TEN_CODE_PATTERN = re.compile(r"绝杀\s*(?:十|10|⑩)\s*码")


def _contains_identity_ten_code(text: str) -> bool:
    return bool(IDENTITY_TEN_CODE_PATTERN.search(normalize_keyword(text)))


def _identity_title_match(lines: list[str], identity: str) -> re.Match | None:
    for line in lines[:20]:
        if normalize_keyword(identity) in normalize_keyword(line):
            continue
        # 站方标题可能写「293期：…」也可能写「293：【…】」（漏掉「期」字），
        # 期号后允许「期」或紧跟冒号，其余契约不变。
        match = re.search(
            r"(?<!\d)0?(\d{1,3})\s*(?:期|[:：]).{0,60}绝杀\s*(?:十|10|⑩)\s*码",
            line,
        )
        if match:
            return match
    return None


def normalize_identity_article_current_placeholder(
    text: str,
    target: dict,
) -> str:
    identity = str(target.get("article_identity") or "").strip()
    page_text = html_to_text(text)
    if identity != "铭记于心":
        return page_text

    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    if len(lines) < 3:
        raise ValueError("铭记于心接口正文结构不完整")
    title_match = _identity_title_match(lines, identity)
    if not title_match:
        raise ValueError("铭记于心没有找到接口当前期绝杀十码标题")
    current_issue = normalize_issue(title_match.group(1))
    previous_issue = str(int(current_issue) - 1)

    placeholder_pattern = re.compile(
        r"(?m)^\s*水期(?=\s*[:：]\s*《\s*铭记于心\s*》"
        r".{0,30}绝杀\s*(?:十|10|⑩)\s*码.{0,30}开\s*[:：])"
    )
    placeholders = list(placeholder_pattern.finditer(page_text))
    if len(placeholders) > 1:
        raise ValueError(f"铭记于心当前期占位行数量异常：{len(placeholders)}")

    numbered_rows = [
        (normalize_issue(match.group(1)), match.start())
        for match in all_issue_segment_matches(page_text)
        if "铭记于心" in normalize_keyword(match.group(0))
        and _contains_identity_ten_code(match.group(0))
    ]
    if not numbered_rows:
        raise ValueError("铭记于心没有找到历史专属数据行")
    if not placeholders:
        if not any(issue == current_issue for issue, _position in numbered_rows):
            raise ValueError(
                f"铭记于心当前期 {current_issue}期既没有数字数据也没有水期占位"
            )
        return page_text

    last_issue, last_position = numbered_rows[-1]
    if last_issue != previous_issue or last_position >= placeholders[0].start():
        raise ValueError(
            f"铭记于心占位行前一期异常：期望 {previous_issue}期，实际 {last_issue}期"
        )
    if any(issue == current_issue for issue, _position in numbered_rows):
        raise ValueError(f"铭记于心 {current_issue}期同时存在数字期号与水期占位，已拒绝")

    placeholder = placeholders[0]
    return page_text[:placeholder.start()] + current_issue + "期" + page_text[placeholder.end():]


def identity_article_top_10_candidates(text: str, target: dict) -> dict[str, list[list[str]]]:
    identity = str(target.get("article_identity") or "").strip()
    if not identity:
        raise ValueError("身份文章顶部专属解析缺少 article_identity")
    if normalize_region(target.get("region")) != "top":
        raise ValueError(f"{identity}专属解析只允许 top")
    if target.get("count") != 10:
        raise ValueError(f"{identity}专属解析要求 count=10")

    page_text = html_to_text(text)
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    normalized_identity = normalize_keyword(identity)
    header_lines = lines[:20]
    if not any(
        normalized_identity in normalize_keyword(line)
        and _contains_identity_ten_code(line)
        for line in header_lines
    ):
        raise ValueError(f"{identity}接口作者身份不匹配")

    rows: list[tuple[str, list[list[str]], int]] = []
    for match in all_issue_segment_matches(page_text):
        segment = match.group(0)
        compact = normalize_keyword(segment)
        identity_position = compact.find(normalized_identity)
        if identity_position < 0 or identity_position > 40:
            continue
        groups: list[list[str]] = []
        for numbers in find_number_groups(segment):
            if len(numbers) != 10 or any(not valid_number(number) for number in numbers):
                continue
            if numbers not in groups:
                groups.append(numbers)
        if groups:
            rows.append((normalize_issue(match.group(1)), groups, match.start()))

    if not rows:
        raise ValueError(f"{identity}没有找到历史专属数据行")

    window = _configured_candidate_window(target, f"{identity}专属文章顶部")
    selected = rows[:window]
    candidates: dict[str, list[list[str]]] = {}
    for issue, groups, _position in selected:
        if not groups:
            continue
        if any(has_duplicate_numbers(numbers) for numbers in groups):
            raise ValueError(f"{issue}期 号码有重复，已停止输出避免抓错")
        candidates.setdefault(issue, []).extend(groups)
    return candidates


def identity_article_top_10_available_issues(text: str, target: dict) -> list[str]:
    return list(identity_article_top_10_candidates(text, target))


def extract_identity_article_top_10_numbers(
    text: str,
    issues: list[str],
    target: dict,
) -> dict[str, list[str]]:
    candidates = identity_article_top_10_candidates(text, target)
    found: dict[str, list[str]] = {}
    for issue in (normalize_issue(item) for item in issues):
        distinct: list[list[str]] = []
        for numbers in candidates.get(issue, []):
            if numbers not in distinct:
                distinct.append(numbers)
        if len(distinct) > 1:
            preview = " | ".join(",".join(numbers) for numbers in distinct[:5])
            raise ValueError(f"{issue}期 候选不唯一，已停止输出避免抓错：{preview}")
        if distinct:
            found[issue] = distinct[0]
    return found


def identity_article_bottom_10_candidates(
    text: str,
    target: dict,
    requested_issues: list[str] | None = None,
) -> dict[str, list[list[str]]]:
    identity = str(target.get("article_identity") or "").strip()
    if not identity:
        raise ValueError("身份文章专属解析缺少 article_identity")
    if normalize_region(target.get("region")) != "bottom":
        raise ValueError(f"{identity}专属解析只允许 bottom")
    if target.get("count") != 10:
        raise ValueError(f"{identity}专属解析要求 count=10")

    page_text = normalize_identity_article_current_placeholder(text, target)
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    normalized_identity = normalize_keyword(identity)
    # 行标识锚点：作者身份仍是 article_identity（决定文章归属），但数据行可能由
    # 同文章内另一署名发布（例如「勤能补拙」文章尾部改写为《横财发家》的十码行）。
    # 定位规则：优先按 row_anchor；该行没有 row_anchor 就退回作者署名，兼容改名
    # 之前的历史期；同一行两者同时出现说明来源不明，立刻停止该行避免抓错。
    row_anchor = str(target.get("row_anchor") or "").strip()
    normalized_row_anchor = normalize_keyword(row_anchor) if row_anchor else ""
    header_lines = lines[:20]
    if not any(
        normalize_keyword(line) == normalized_identity
        or normalize_keyword(line).startswith(f"作者{normalized_identity}")
        for line in header_lines
    ):
        raise ValueError(f"{identity}接口作者身份不匹配")
    heading = _identity_title_match(lines, identity)
    if not heading:
        raise ValueError(f"{identity}没有找到接口当前期绝杀十码标题")

    requested_issue_set = {
        normalize_issue(issue)
        for issue in (requested_issues or [])
        if str(issue).strip()
    }
    skip_historical_duplicate_rows = target.get("skip_historical_duplicate_rows") is True
    # (期数, 号码组, 位置, 有重复, 两署名并存)
    rows: list[tuple[str, list[list[str]], int, bool, bool]] = []
    for match in all_issue_segment_matches(page_text):
        segment = match.group(0)
        compact = normalize_keyword(segment)
        row_position = compact.find(normalized_row_anchor) if normalized_row_anchor else -1
        identity_position = compact.find(normalized_identity)
        both_signatures = (
            bool(normalized_row_anchor)
            and normalized_row_anchor != normalized_identity
            and row_position >= 0
            and identity_position >= 0
        )
        if both_signatures:
            # 历史行里一条歧义不应把当期一起拖死，所以这里只登记，
            # 等窗口选完、只对真正会用到的行拒绝。
            rows.append((normalize_issue(match.group(1)), [], match.start(), False, True))
            continue
        anchor_position = row_position if row_position >= 0 else identity_position
        if anchor_position < 0 or anchor_position > 40 or not _contains_identity_ten_code(segment):
            continue
        groups: list[list[str]] = []
        for numbers in find_number_groups(segment):
            if len(numbers) != 10 or any(not valid_number(number) for number in numbers):
                continue
            if numbers not in groups:
                groups.append(numbers)
        rows.append(
            (
                normalize_issue(match.group(1)),
                groups,
                match.start(),
                any(has_duplicate_numbers(numbers) for numbers in groups),
                False,
            )
        )

    window = _configured_candidate_window(target, f"{identity}专属文章尾部")
    # 歧义行只参与判定、不占窗口名额：否则它会把窗口里最老的一条有效行挤出去，
    # 那一期就会被误报成“没有找到指定期数”。
    valid_rows = [row for row in rows if not row[4]]
    window_rows = valid_rows
    if skip_historical_duplicate_rows:
        window_rows = [
            row
            for row in valid_rows
            if not row[3] or row[0] in requested_issue_set
        ]
    selected = window_rows[-window:]
    boundary = selected[0][2] if selected else None
    ambiguous_issues = unique_keep_order(
        [
            row[0]
            for row in rows
            if row[4]
            and (
                row[0] in requested_issue_set
                or (boundary is not None and row[2] >= boundary)
            )
        ]
    )
    if ambiguous_issues:
        raise ValueError(
            f"{','.join(ambiguous_issues)}期 同一行同时出现行标识{row_anchor}"
            f"与作者署名{identity}，已停止输出避免抓错"
        )
    candidates: dict[str, list[list[str]]] = {}
    for issue, groups, _position, has_duplicate, _ambiguous in selected:
        if not groups:
            continue
        if has_duplicate:
            raise ValueError(f"{issue}期 号码有重复，已停止输出避免抓错")
        if len(groups) > 1:
            preview = " | ".join(",".join(numbers) for numbers in groups[:5])
            raise ValueError(f"{issue}期 候选不唯一，已停止输出避免抓错：{preview}")
        candidates.setdefault(issue, []).append(groups[0])
    return candidates


def identity_article_bottom_10_available_issues(text: str, target: dict) -> list[str]:
    return list(identity_article_bottom_10_candidates(text, target))


def extract_identity_article_bottom_10_numbers(
    text: str,
    issues: list[str],
    target: dict,
) -> dict[str, list[str]]:
    candidates = identity_article_bottom_10_candidates(text, target, issues)
    found: dict[str, list[str]] = {}
    for issue in (normalize_issue(item) for item in issues):
        distinct: list[list[str]] = []
        for numbers in candidates.get(issue, []):
            if numbers not in distinct:
                distinct.append(numbers)
        if len(distinct) > 1:
            preview = " | ".join(",".join(numbers) for numbers in distinct[:5])
            raise ValueError(f"{issue}期 候选不唯一，已停止输出避免抓错：{preview}")
        if distinct:
            found[issue] = distinct[0]
    return found


def extract_babu_maoge_must_ten_numbers(
    text: str,
    issues: list[str],
    target: dict,
) -> dict[str, list[str]]:
    if normalize_region(target.get("region")) != "top":
        raise ValueError("八步毛哥必杀十码专属解析只允许 top")
    return extract_issue_numbers(
        text,
        issues,
        keywords=target.get("keywords"),
        expected_count=target.get("count"),
        strict_ambiguous=True,
        anchor=target.get("anchor"),
        region="top",
        issue_position_window=CANDIDATE_REGION_WINDOW,
    )
