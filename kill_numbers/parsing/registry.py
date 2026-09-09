from collections.abc import Callable
from dataclasses import dataclass

from kill_numbers.parsing.common import extract_issue_numbers
from kill_numbers.parsing.diagnostics import detect_available_issues
from kill_numbers.parsing.dedicated import site_parsers as dedicated


ParseFunction = Callable[[str, list[str], dict], dict[str, list[str]]]
AvailableFunction = Callable[[str, dict], list[str]]


@dataclass(frozen=True)
class ParserAdapter:
    parse: ParseFunction
    available: AvailableFunction | None = None


def parse_generic(content: str, issues: list[str], target: dict) -> dict[str, list[str]]:
    return extract_issue_numbers(
        content,
        issues,
        keywords=target.get("keywords"),
        expected_count=target.get("count"),
        position=target.get("position", "first"),
        strict_ambiguous=not target.get("allow_ambiguous", False),
        allow_duplicate_numbers=target.get("allow_duplicate_numbers", False),
        anchor=target.get("anchor"),
        stop_anchor=target.get("stop_anchor"),
        region=target.get("region"),
        issue_position_window=target.get("issue_position_window"),
        allowed_row_starts=target.get("_allowed_row_starts"),
    )


def available_generic(content: str, target: dict) -> list[str]:
    return detect_available_issues(
        content,
        keywords=target.get("keywords"),
        expected_count=target.get("count"),
        position=target.get("position", "first"),
        allow_duplicate_numbers=target.get("allow_duplicate_numbers", False),
        anchor=target.get("anchor"),
        stop_anchor=target.get("stop_anchor"),
        region=target.get("region"),
        issue_position_window=target.get("issue_position_window"),
        allowed_row_starts=target.get("_allowed_row_starts"),
    )


def _count_parser(function: Callable) -> ParseFunction:
    return lambda content, issues, target: function(
        content,
        issues,
        expected_count=target.get("count"),
        issue_position_window=target.get("issue_position_window"),
    )


def _count_region_parser(function: Callable) -> ParseFunction:
    return lambda content, issues, target: function(
        content,
        issues,
        expected_count=target.get("count"),
        region=target.get("region"),
        issue_position_window=target.get("issue_position_window"),
    )


def _target_parser(function: Callable) -> ParseFunction:
    return lambda content, issues, target: function(content, issues, target)


def _target_available(function: Callable) -> AvailableFunction:
    return lambda content, target: list(function(content, target))


def _dedicated_parser(parser_name: str) -> ParseFunction:
    return lambda content, issues, target: dedicated.extract_dedicated_ten_numbers(
        content,
        issues,
        target,
        parser_name,
    )


def _dedicated_available(parser_name: str) -> AvailableFunction:
    return lambda content, target: dedicated.dedicated_ten_available_issues(
        content,
        target,
        parser_name,
    )


PARSERS: dict[str, ParserAdapter] = {
    "macau_baoma": ParserAdapter(_count_region_parser(dedicated.extract_macau_baoma_numbers)),
    "zuibaxian_top7": ParserAdapter(_count_parser(dedicated.extract_zuibaxian_top7_numbers)),
    "white_tiger_stable_10": ParserAdapter(_count_parser(dedicated.extract_white_tiger_stable_10_numbers)),
    "top_article_history": ParserAdapter(_target_parser(dedicated.extract_top_article_history_numbers)),
    "top_article_history_current_cycle": ParserAdapter(
        _target_parser(dedicated.extract_top_article_history_current_cycle_numbers)
    ),
    "huxin_xiaozhu_stable_10": ParserAdapter(
        _target_parser(dedicated.extract_huxin_xiaozhu_stable_10_numbers),
        _target_available(dedicated.huxin_xiaozhu_stable_10_available_issues),
    ),
    "babu_maoge_must_ten": ParserAdapter(
        _target_parser(dedicated.extract_babu_maoge_must_ten_numbers)
    ),
    "xinzhu_forum_stable_10": ParserAdapter(
        _target_parser(dedicated.extract_xinzhu_forum_stable_10_numbers),
        _target_available(dedicated.xinzhu_forum_top_candidates),
    ),
    "liuhe_bottom_10": ParserAdapter(
        _target_parser(dedicated.extract_liuhe_bottom_10_numbers),
        _target_available(dedicated.liuhe_bottom_10_available_issues),
    ),
    "identity_article_bottom_10": ParserAdapter(
        _target_parser(dedicated.extract_identity_article_bottom_10_numbers),
        _target_available(dedicated.identity_article_bottom_10_available_issues),
    ),
    "identity_article_top_10": ParserAdapter(
        _target_parser(dedicated.extract_identity_article_top_10_numbers),
        _target_available(dedicated.identity_article_top_10_available_issues),
    ),
    "fengwu_jiutian_bottom_10": ParserAdapter(
        _target_parser(dedicated.extract_fengwu_jiutian_bottom_10_numbers),
        _target_available(dedicated.fengwu_jiutian_bottom_10_available_issues),
    ),
    "qiancai_liangde_bottom_10": ParserAdapter(
        _target_parser(dedicated.extract_qiancai_liangde_bottom_10_numbers),
        _target_available(dedicated.qiancai_liangde_bottom_10_available_issues),
    ),
}

for _parser_name in dedicated.DEDICATED_TEN_ROW_CONTRACTS:
    PARSERS[_parser_name] = ParserAdapter(
        _dedicated_parser(_parser_name),
        _dedicated_available(_parser_name),
    )


ACQUISITION_ONLY_PARSERS = {
    "zuojianzifu_link_chain",
    "ttss_paginated_identity_top_10",
}
VALID_SPECIAL_PARSERS = frozenset(PARSERS) | ACQUISITION_ONLY_PARSERS


def parser_adapter(target: dict) -> ParserAdapter:
    parser_name = str(target.get("special_parser") or "")
    if not parser_name:
        return ParserAdapter(parse_generic, available_generic)
    if parser_name in ACQUISITION_ONLY_PARSERS:
        raise ValueError(f"采集专属解析器不能直接进入正文解析：{parser_name}")
    adapter = PARSERS.get(parser_name)
    if adapter is None:
        raise ValueError(f"未注册专属解析器：{parser_name}")
    return adapter


def parse_target_content(
    content: str,
    target: dict,
    issues: list[str],
) -> dict[str, list[str]]:
    return parser_adapter(target).parse(content, issues, target)


def available_issues_from_content(content: str, target: dict) -> list[str]:
    adapter = parser_adapter(target)
    if adapter.available is not None:
        return adapter.available(content, target)
    # Replay the exact dedicated parser over the period labels in this document;
    # do not route dedicated history/HTML structures through generic discovery.
    import re
    from kill_numbers.text_utils import html_to_text, normalize_issue, unique_keep_order
    issues = unique_keep_order(normalize_issue(m.group(1)) for m in
        re.finditer(r"(?<!\d)0?(\d{1,3})\s*期", html_to_text(content)))
    found = adapter.parse(content, issues, target)
    return [issue for issue in issues if issue in found]
