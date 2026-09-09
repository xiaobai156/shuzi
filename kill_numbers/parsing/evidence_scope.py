"""Parser-owned section boundaries used to verify successful provenance."""
import re

from kill_numbers.parsing import dedicated
from kill_numbers.parsing.dedicated import site_parsers as sites
from kill_numbers.parsing.common import (
    candidate_rows, find_anchor_index, html_to_text, normalize_issue,
    normalize_region, scope_text_by_anchor_with_offset,
)


def evidence_section(content: str, target: dict) -> tuple[str, str, int]:
    parser = target.get("special_parser", "")
    text = html_to_text(content)
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
        section, start = scope_text_by_anchor_with_offset(text, target.get("anchor"), target.get("stop_anchor"))
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
        seams = [rows[i].start for i in range(1, len(rows))
                 if rows[i - 1].issue == "365" and rows[i].issue == "1"]
        if len(seams) != 1:
            raise ValueError("来源证据周期边界不唯一")
        if normalize_region(target.get("region")) == "top":
            section = section[:seams[0]]
        else:
            start += seams[0]
            section = section[seams[0]:]
    return text, section, start
