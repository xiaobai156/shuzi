"""Parser-owned section boundaries used to verify successful provenance."""
import re

from kill_numbers.parsing import dedicated
from kill_numbers.parsing.dom_scope import content_section
from kill_numbers.parsing.dedicated import site_parsers as sites
from kill_numbers.parsing.common import (
    anchor_scope_candidates,
    candidate_rows,
    find_anchor_index,
    html_to_text,
    normalize_issue,
    normalize_region,
    scope_text_by_anchor_with_offset,
    windowed_rows,
)
from kill_numbers.parsing.errors import AmbiguousSourceError, SourceContractError
from kill_numbers.domain.periods import rollover_seam_indices


def evidence_section(
    content: str,
    target: dict,
    issue: str | None = None,
    numbers: list[str] | tuple[str, ...] | None = None,
) -> tuple[str, str, int]:
    parser = target.get("special_parser", "")
    text = html_to_text(content)
    if target.get("content_class"):
        section, offset = content_section(content, target)
        return text, section, offset
    if parser == "identity_article_bottom_10":
        text = sites.normalize_identity_article_current_placeholder(text, target)
    section = None
    if parser in sites.DEDICATED_TEN_ROW_CONTRACTS:
        contract = sites.DEDICATED_TEN_ROW_CONTRACTS[parser]
        headings = list(contract["heading"].finditer(text))
        if len(headings) != 1:
            raise ValueError("来源证据专属栏目不唯一")
        stop = contract["stop"].search(text, headings[0].end())
        if not stop and parser != "shita_top_10":
            raise ValueError("来源证据缺少专属结束边界")
        section = text[headings[0].start():stop.start() if stop else len(text)]
    elif parser == "identity_article_top_10":
        # The dedicated parser validates the API/article identity. Repeated
        # author text inside each data row is not a section boundary.
        sites.identity_article_top_10_candidates(content, target)
        section = text
    elif parser == "identity_article_bottom_10":
        sites.identity_article_bottom_10_candidates(content, target)
        section = text
    elif parser == "huxin_xiaozhu_stable_10":
        section = sites.huxin_xiaozhu_stable_10_section(content, target)
    elif parser == "fengwu_jiutian_bottom_10":
        section = sites.fengwu_jiutian_bottom_10_section(content, target)
    elif parser == "zuibaxian_top7":
        headings = list(re.finditer(r"<div\b[^>]*\bid=['\"]top_7['\"][^>]*>", content, re.I))
        if len(headings) != 1:
            raise ValueError("来源证据 top_7 不唯一")
        start = headings[0].end()
        stop = re.search(r"<div\b[^>]*\bid=['\"]top_\d+['\"][^>]*>", content[start:], re.I)
        section = html_to_text(content[start:start + stop.start() if stop else len(content)])
    elif parser == "white_tiger_stable_10":
        heading = re.search(r"(?<!\d)\d{1,3}\s*期\s*稳杀10码", text)
        if not heading:
            raise ValueError("来源证据缺少白虎标题")
        stop = find_anchor_index(text, "上一篇", heading.end())
        if stop < 0:
            raise ValueError("来源证据缺少白虎结束边界")
        section = text[heading.start():stop]
    elif parser == "xinzhu_forum_stable_10":
        heading = re.search(r"精英榜\s*0?\d{1,3}\s*期\s*[:：]\s*[\[【]\s*绝杀十码\s*[\]】]\s*已公开", text)
        if not heading:
            raise ValueError("来源证据缺少新竹标题")
        stop = re.search(r"(?m)^\s*(?:上|下)一篇\s*[:：]", text[heading.end():])
        if not stop:
            raise ValueError("来源证据缺少新竹结束边界")
        section = text[heading.end():heading.end() + stop.start()]
    elif parser == "liuhe_bottom_10":
        heading = re.search(r"(?m)^\s*0?\d{1,3}\s*期\s*绝杀\s*(?:十|10)\s*码\s*$", text)
        if not heading:
            raise ValueError("来源证据缺少六合标题")
        section = text[heading.end():]
    elif parser == "macau_baoma":
        matches = list(re.finditer(
            r"<span[^>]*class=['\"]am_qi['\"][^>]*>\s*0?\d{1,3}\s*期\s*</span>\s*"
            r"<span[^>]*class=['\"]am_yc['\"][^>]*>.*?</span>", content, re.I | re.S))
        if not matches:
            raise ValueError("来源证据缺少报码结构")
        section = html_to_text(content[matches[0].start():matches[-1].end()])
    if section is None:
        scopes = anchor_scope_candidates(
            text,
            target.get("anchor"),
            target.get("stop_anchor"),
        )
        if len(scopes) == 1:
            section, start = scopes[0].text, scopes[0].start
        elif issue is not None and numbers is not None:
            expected = tuple(numbers)
            matching = []
            for scope in scopes:
                rows = list(
                    candidate_rows(
                        scope.text,
                        target.get("keywords"),
                        target.get("count"),
                        target.get("allow_duplicate_numbers", False),
                    )
                )
                window = (
                    target.get("_history_depth")
                    if target.get("_history_discovery") is True
                    else target.get("issue_position_window")
                )
                selected = windowed_rows(rows, target.get("region"), window)
                if any(
                    row.issue == normalize_issue(issue) and expected in row.groups
                    for row in selected
                ):
                    signature = tuple(
                        (row.issue, row.groups)
                        for row in selected
                    )
                    matching.append((scope, signature))
            if not matching:
                raise SourceContractError(
                    f"{normalize_issue(issue)}期在所有完整锚点区块中均缺少候选"
                )
            signatures = {signature for _scope, signature in matching}
            if len(signatures) > 1:
                raise AmbiguousSourceError(
                    "多个完整锚点区块包含目标候选，但区块内容不一致"
                )
            chosen, _signature = min(
                matching,
                key=lambda item: (
                    item[0].end - item[0].start,
                    item[0].start,
                ),
            )
            section, start = chosen.text, chosen.start
        else:
            raise AmbiguousSourceError(
                f"正文锚点对应 {len(scopes)} 个完整区块，缺少候选信息无法判定"
            )
    else:
        # Normalization can remove padding; a repeated identical section is
        # ambiguous provenance rather than a reason to choose its first copy.
        section = section.strip()
        start = text.find(section) if section else -1
        if start < 0 or text.find(section, start + 1) >= 0:
            raise ValueError("来源证据专属区块偏移不唯一")
    if parser == "top_article_history_current_cycle":
        rows = list(candidate_rows(section, target.get("keywords"), target.get("count"),
                                   target.get("allow_duplicate_numbers", False)))
        seam_indices = rollover_seam_indices([row.issue for row in rows], target)
        seams = [rows[index].start for index in seam_indices]
        if len(seams) != 1:
            raise ValueError("来源证据周期边界不唯一")
        if normalize_region(target.get("region")) == "top":
            section = section[:seams[0]]
        else:
            start += seams[0]
            section = section[seams[0]:]
    return text, section, start
