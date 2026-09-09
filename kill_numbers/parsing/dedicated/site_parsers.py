import re

from kill_numbers.parsing.common import (
    CANDIDATE_REGION_WINDOW,
    resolve_candidate_window,
    all_issue_segment_matches,
    extract_issue_numbers,
    find_anchor_index,
    find_last_anchor_index_before,
    find_number_groups,
    filter_candidates_by_region,
    has_duplicate_numbers,
    issue_segment_matches,
    normalize_region,
    valid_number,
)
from kill_numbers.parsing.diagnostics import detect_available_issues
from kill_numbers.text_utils import html_to_text, normalize_issue, normalize_keyword


def _configured_candidate_window(target: dict, label: str) -> int:
    return resolve_candidate_window(target.get("issue_position_window"))


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


def extract_macau_baoma_numbers(
    text: str,
    issues: list[str],
    expected_count: int | None = None,
    region: str | None = None,
    issue_position_window: int | None = None,
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
        raw_numbers = html_to_text(match.group(2)).strip().strip("[]【】")
        if not re.fullmatch(r"\d{2}(?:[\s.,，。、;；|/\\]+\d{2})*", raw_numbers):
            continue
        numbers = re.findall(r"\d{2}", raw_numbers)
        if any(not valid_number(number) for number in numbers) or has_duplicate_numbers(numbers):
            continue
        if expected_count and len(numbers) != expected_count:
            continue
        rows.append((numbers, issue, match.start()))

    selected_rows = filter_candidates_by_region(
        [(numbers, issue, position) for numbers, issue, position in rows],
        region,
        strict_window=True,
        require_region=True,
        issue_position_window=issue_position_window,
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
    issue_position_window: int | None = None,
) -> dict[str, list[str]]:
    if issue_position_window is None:
        issue_position_window = 3  # The top_7 contract has an explicit three-row boundary.
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
        issue_position_window=issue_position_window,
    )


def extract_white_tiger_stable_10_numbers(
    text: str,
    issues: list[str],
    expected_count: int | None = 10,
    issue_position_window: int | None = None,
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
        issue_position_window=issue_position_window,
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
    if stop_anchor and stop_index < 0:
        raise ValueError("没有找到正文结束锚点")
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
    """Extract current-cycle rows when the page also embeds a legacy cycle."""
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
    if len(cycle_seams) != 1:
        raise ValueError(f"周期文档边界数量异常：{len(cycle_seams)}")

    seam = cycle_seams[0]
    blocks = [rows[:seam], rows[seam:]]
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
        distinct = []
        for issue, numbers, _position in directional_blocks[primary_block_index]:
            if issue == requested_issue and numbers not in distinct:
                distinct.append(numbers)
        if len(distinct) > 1:
            raise ValueError(f"{requested_issue}期 候选不唯一，已停止输出避免抓错")
        if distinct:
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
        issue_position_window=target.get("issue_position_window"),
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
        issue_position_window=target.get("issue_position_window"),
    )


def xinzhu_forum_top_candidates(text: str, target: dict) -> dict[str, list[list[str]]]:
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
    if len(headings) != 1:
        raise ValueError("新竹论坛专属栏目标题不唯一或不存在")

    window = _configured_candidate_window(target, "新竹论坛顶部")

    candidates: dict[str, list[list[str]]] = {}
    for heading in headings:
        stop = re.search(r"(?m)^\s*(?:上|下)一篇\s*[:：]", page_text[heading.end():])
        if not stop:
            raise ValueError("新竹论坛没有找到栏目停止边界：上一篇")
        section = page_text[heading.end():heading.end() + stop.start()]
        valid_rows = [row for row in row_pattern.finditer(section)
                      if all(valid_number(n) for n in re.findall(r"\d{2}", row.group(2)))
                      and not has_duplicate_numbers(re.findall(r"\d{2}", row.group(2)))]
        for row in valid_rows[:window]:
            issue = normalize_issue(row.group(1))
            numbers = re.findall(r"\d{2}", row.group(2))
            if len(numbers) != 10 or any(not valid_number(number) for number in numbers):
                continue
            if has_duplicate_numbers(numbers):
                raise ValueError(f"{issue}期 号码有重复，已停止输出避免抓错")
            candidates.setdefault(issue, []).append(numbers)
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
    rows: list[tuple[str, list[str]]] = []
    for row in row_pattern.finditer(page_text[headings[0].end():]):
        numbers = re.findall(r"\d{2}", row.group(2))
        if len(numbers) != 10 or any(not valid_number(number) for number in numbers):
            continue
        if has_duplicate_numbers(numbers):
            continue
        rows.append((normalize_issue(row.group(1)), numbers))

    window = _configured_candidate_window(target, "六合绝杀十码尾部")
    candidates: dict[str, list[list[str]]] = {}
    for issue, numbers in rows[-window:]:
        candidates.setdefault(issue, []).append(numbers)

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


def dedicated_ten_row_candidates(text: str, target: dict, parser_name: str) -> dict[str, list[list[str]]]:
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
    rows = [row for row in rows
            if all(valid_number(n) for n in re.findall(r"\d{2}", row.group(2)))
            and not has_duplicate_numbers(re.findall(r"\d{2}", row.group(2)))]
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
    return candidates


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
    for issue, numbers, _position in rows[-_configured_candidate_window(target, "钱彩两得"):]:
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
        match = re.search(
            r"(?<!\d)0?(\d{1,3})\s*期.{0,60}绝杀\s*(?:十|10|⑩)\s*码",
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
            if has_duplicate_numbers(numbers):
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


def identity_article_bottom_10_candidates(text: str, target: dict) -> dict[str, list[list[str]]]:
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

    rows: list[tuple[str, list[list[str]], int]] = []
    for match in all_issue_segment_matches(page_text):
        segment = match.group(0)
        compact = normalize_keyword(segment)
        identity_position = compact.find(normalized_identity)
        if identity_position < 0 or identity_position > 40 or not _contains_identity_ten_code(segment):
            continue
        groups: list[list[str]] = []
        for numbers in find_number_groups(segment):
            if len(numbers) != 10 or any(not valid_number(number) for number in numbers):
                continue
            if has_duplicate_numbers(numbers):
                continue
            if numbers not in groups:
                groups.append(numbers)
        if groups:
            rows.append((normalize_issue(match.group(1)), groups, match.start()))

    window = _configured_candidate_window(target, f"{identity}专属文章尾部")
    selected = rows[-window:]
    candidates: dict[str, list[list[str]]] = {}
    for issue, groups, _position in selected:
        if not groups:
            continue
        if any(has_duplicate_numbers(numbers) for numbers in groups):
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
    candidates = identity_article_bottom_10_candidates(text, target)
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
        issue_position_window=target.get("issue_position_window"),
    )
