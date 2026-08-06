import pytest

import crawler
from kill_numbers import text_utils
from kill_numbers.parsing import common


def test_crawler_reexports_text_and_common_helpers():
    assert crawler.html_to_text is text_utils.html_to_text
    assert crawler.normalize_issue is text_utils.normalize_issue
    assert crawler.extract_issue_numbers is common.extract_issue_numbers
    assert crawler.find_number_groups is common.find_number_groups


def test_text_normalization_keeps_existing_keyword_behavior():
    assert text_utils.normalize_keyword("【絕殺十碼】") == "绝杀10码"
    assert text_utils.parse_issues("009期, 010 011期") == ["9", "10", "11"]
    assert text_utils.html_to_text("<p>第一行<br>第二行</p>") == "第一行\n第二行"


def test_common_parser_preserves_order_and_fails_on_conflict():
    content = """栏目起点
211期:专属绝杀十码开:0000准
[09.01.22.13.44.05.36.17.28.49]
栏目终点
"""
    found = common.extract_issue_numbers(
        content,
        ["211"],
        keywords=["专属绝杀十码"],
        expected_count=10,
        strict_ambiguous=True,
        anchor="栏目起点",
        stop_anchor="栏目终点",
        region="top",
    )
    assert found["211"] == ["09", "01", "22", "13", "44", "05", "36", "17", "28", "49"]

    conflict = content.replace(
        "栏目终点",
        "211期:专属绝杀十码开:0000准\n"
        "[01.02.03.04.05.06.07.08.09.10]\n栏目终点",
    )
    with pytest.raises(ValueError, match="候选不唯一"):
        common.extract_issue_numbers(
            conflict,
            ["211"],
            keywords=["专属绝杀十码"],
            expected_count=10,
            strict_ambiguous=True,
            anchor="栏目起点",
            stop_anchor="栏目终点",
            region="top",
        )


def test_anchor_scope_never_falls_back_to_unscoped_text():
    with pytest.raises(ValueError, match="没有找到正文锚点"):
        common.extract_issue_numbers(
            "211期:绝杀十码 01.02.03.04.05.06.07.08.09.10",
            ["211"],
            keywords=["绝杀十码"],
            expected_count=10,
            anchor="不存在的专属锚点",
        )
