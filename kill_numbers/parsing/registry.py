from collections.abc import Callable
from dataclasses import dataclass
import re

from kill_numbers.parsing.common import extract_issue_numbers, target_issue_region
from kill_numbers.parsing.diagnostics import detect_available_issues
from kill_numbers.parsing.dom_scope import content_section
from kill_numbers.parsing.dedicated import site_parsers as dedicated
from kill_numbers.text_utils import normalize_keyword, origin


def target_for_document(target: dict, document) -> dict:
    """Only a verified user API identity can replace an absent inline author."""
    scoped = dict(target)
    if 'allowed_row_starts' in document.metadata:
        scoped['_allowed_row_starts'] = document.metadata['allowed_row_starts']
    if scoped.get('source_url_pattern') and scoped.get('source_anchor'):
        scoped['anchor'] = scoped['source_anchor']
    user = re.search(r'/users/(\d+)(?:[/?#]|$)', str(target.get('url') or ''))
    if (user and document.kind == 'user_forum_topic'
            and document.metadata.get('identity_verified') is True
            and document.metadata.get('user_id') == user.group(1)
            and document.metadata.get('identity_source') == f"{origin(target['url'])}/api/v1/users/{user.group(1)}"
            and document.identity
            and normalize_keyword(document.identity) == normalize_keyword(str(scoped.get('anchor') or ''))):
        scoped['anchor'] = ''
    return scoped


ParseFunction = Callable[[str, list[str], dict], dict[str, list[str]]]
AvailableFunction = Callable[[str, dict], list[str]]


@dataclass(frozen=True)
class ParserAdapter:
    parse: ParseFunction
    available: AvailableFunction | None = None


def parse_generic(content: str, issues: list[str], target: dict) -> dict[str, list[str]]:
    if target.get('content_class'):
        content, offset = content_section(content, target)
        target = {**target, 'anchor': None, 'stop_anchor': None}
        if '_allowed_row_starts' in target:
            target['_allowed_row_starts'] = {start - offset for start in target['_allowed_row_starts']}
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
        region=target_issue_region(target),
        issue_position_window=target.get("issue_position_window"),
        allowed_row_starts=target.get('_allowed_row_starts'),
        prefer_pending=target.get("prefer_pending_candidate", False),
    )


def available_generic(content: str, target: dict) -> list[str]:
    if target.get('content_class'):
        content, offset = content_section(content, target)
        target = {**target, 'anchor': None, 'stop_anchor': None, 'content_class': None}
        if '_allowed_row_starts' in target:
            target['_allowed_row_starts'] = {start - offset for start in target['_allowed_row_starts']}
    available = detect_available_issues(
        content,
        keywords=target.get("keywords"),
        expected_count=target.get("count"),
        position=target.get("position", "first"),
        allow_duplicate_numbers=target.get("allow_duplicate_numbers", False),
        anchor=target.get("anchor"),
        stop_anchor=target.get("stop_anchor"),
        region=target_issue_region(target),
        issue_position_window=target.get("issue_position_window"),
    )
    if '_allowed_row_starts' in target:
        return [issue for issue in available if issue in parse_generic(content,[issue],target)]
    return available


def _count_parser(function: Callable) -> ParseFunction:
    return lambda content, issues, target: function(
        content,
        issues,
        expected_count=target.get("count"),
    )


def _count_region_parser(function: Callable) -> ParseFunction:
    return lambda content, issues, target: function(
        content,
        issues,
        expected_count=target.get("count"),
        region=target.get("region"),
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
    "chaoxi_hyphen_top_7": ParserAdapter(
        _target_parser(dedicated.extract_chaoxi_hyphen_top_7_numbers),
        _target_available(dedicated.chaoxi_hyphen_top_7_available_issues),
    ),
    "discuss_detail_top_10": ParserAdapter(
        _target_parser(dedicated.extract_discuss_detail_top_10_numbers),
        available_generic,
    ),
    "discuss_user_release_top_10": ParserAdapter(
        _target_parser(dedicated.extract_discuss_detail_top_10_numbers),
        available_generic,
    ),
    "huaxianzi_bottom_7": ParserAdapter(
        _target_parser(dedicated.extract_huaxianzi_bottom_7_numbers),
        _target_available(dedicated.huaxianzi_bottom_7_available_issues),
    ),
    "fengwu_jiutian_bottom_10": ParserAdapter(
        _target_parser(dedicated.extract_fengwu_jiutian_bottom_10_numbers),
        _target_available(dedicated.fengwu_jiutian_bottom_10_available_issues),
    ),
    "qiancai_liangde_bottom_10": ParserAdapter(
        _target_parser(dedicated.extract_qiancai_liangde_bottom_10_numbers),
        _target_available(dedicated.qiancai_liangde_bottom_10_available_issues),
    ),
    "yiran_gongzhu_bottom_6": ParserAdapter(
        _target_parser(dedicated.extract_yiran_gongzhu_bottom_6_numbers),
        _target_available(dedicated.yiran_gongzhu_bottom_6_available_issues),
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
    available = adapter.available or available_generic
    return available(content, target)
