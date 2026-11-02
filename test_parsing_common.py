import pytest

import crawler
from kill_numbers import text_utils
from kill_numbers.parsing import common


def test_crawler_reexports_text_and_common_helpers():
    assert crawler.html_to_text is text_utils.html_to_text
    assert crawler.normalize_issue is text_utils.normalize_issue
    assert crawler.extract_issue_numbers is common.extract_issue_numbers
    assert crawler.find_number_groups is common.find_number_groups


def test_common_target_metadata_helpers():
    assert common.target_keywords({"keywords": "绝杀10码"}) == ["绝杀10码"]
    assert common.target_keywords({"keywords": ["绝杀10码", ""]}) == ["绝杀10码"]
    assert common.has_broad_keywords({"keywords": ["绝杀10码"]}) is True
    assert common.manual_risk_reason({"keywords": ["绝杀10码"], "allow_ambiguous": True}) == (
        "缺少 anchor、宽关键词、allow_ambiguous"
    )


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


def test_anchor_inside_row_keeps_that_row_issue_label():
    # 锚点落在最新一期数据行内部（期数标签在该行行首）时，标签不能被裁掉。
    content = (
        "栏目前言\n"
        "271期:屠龙宝刀 杀10码 《02.17.42.11.36.39.16.18.19.21》 开:?(准)\n"
        "270期:屠龙宝刀 杀10码 《11.24.03.16.32.33.39.28.22.41》 开:19准\n"
        "栏目终点"
    )

    found = common.extract_issue_numbers(
        content,
        ["271"],
        keywords=["杀10码"],
        expected_count=10,
        strict_ambiguous=True,
        anchor="屠龙宝刀 杀10码",
        stop_anchor="栏目终点",
        region="top",
        issue_position_window=2,
    )

    assert found == {"271": ["02", "17", "42", "11", "36", "39", "16", "18", "19", "21"]}


def test_available_issue_window_without_direction_does_not_crash():
    content = (
        "栏目起点\n"
        "211期:绝杀十码 01.02.03.04.05.06.07.08.09.10\n"
        "210期:绝杀十码 11.12.13.14.15.16.17.18.19.20"
    )

    assert crawler.detect_available_issues(
        content,
        keywords=["绝杀十码"],
        expected_count=10,
        anchor="栏目起点",
        issue_position_window=3,
    ) == ["211", "210"]


def test_invalid_number_is_not_dropped_to_make_a_group_fit_count():
    content = (
        "栏目起点\n"
        "211期:绝杀十码 "
        "01.02.03.04.05.06.07.08.09.10.50\n"
        "栏目终点"
    )

    found = common.extract_issue_numbers(
        content,
        ["211"],
        keywords=["绝杀十码"],
        expected_count=10,
        strict_ambiguous=True,
        anchor="栏目起点",
        stop_anchor="栏目终点",
        region="top",
    )

    assert found == {}


def test_number_groups_accept_plus_separator():
    assert common.find_number_groups(
        "264期:〔冰哥哥〕 13+18+20+26+29+30+31+34+38+45 [杀十码]开:狗21准准"
    ) == [["13", "18", "20", "26", "29", "30", "31", "34", "38", "45"]]


def test_identical_duplicate_issue_row_does_not_consume_direction_window():
    rows = [
        f"{issue}期[绝杀10码]{{01.02.03.04.05.06.07.08.09.{number:02d}}}"
        for issue, number in zip(range(241, 231, -1), range(10, 20))
    ]
    rows.insert(3, rows[2])  # 页面重复展示完全相同的239期。
    content = "作者:测试站\n" + "\n".join(rows) + "\n上一篇:"

    found = common.extract_issue_numbers(
        content,
        ["232", "231"],
        keywords=["绝杀10码"],
        expected_count=10,
        strict_ambiguous=True,
        anchor="作者:测试站",
        stop_anchor="上一篇:",
        region="top",
        issue_position_window=10,
    )

    assert found == {"232": ["01", "02", "03", "04", "05", "06", "07", "08", "09", "19"]}


def test_bottom_window_keeps_last_identical_duplicate_position():
    text = """230期[绝杀10码]{01.02.03.04.05.06.07.08.09.10}
231期[绝杀10码]{11.12.13.14.15.16.17.18.19.20}
230期[绝杀10码]{01.02.03.04.05.06.07.08.09.10}
"""

    starts = common.issue_position_window_starts(
        text,
        ["绝杀10码"],
        10,
        "bottom",
        1,
    )

    assert starts == {text.rfind("230期")}
