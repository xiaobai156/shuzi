# -*- coding: utf-8 -*-
"""号码组排版疏漏的容错切分回归。

站方偶尔少标一个分隔符（把两个号粘成 4 位）或漏印前导 0（只写 1 位），
严格切分会把整组判废，整期取不到值。容错切分只在这两种情况下生效，
且只在「本段严格切分一个够数的号码组都没有」时才启用。
"""
import pytest

from kill_numbers.parsing.common import (
    expand_number_tokens,
    extract_issue_numbers,
    find_number_groups,
    number_groups_for_count,
)


class TestExpandNumberTokens:
    def test_strict_keeps_normal_two_digit_tokens(self):
        assert expand_number_tokens(["05", "17", "29"], False) == ["05", "17", "29"]

    def test_strict_rejects_glued_four_digit(self):
        assert expand_number_tokens(["28", "4002", "14"], False) is None

    def test_strict_rejects_single_digit(self):
        assert expand_number_tokens(["48", "46", "1"], False) is None

    def test_relaxed_splits_glued_four_digit(self):
        assert expand_number_tokens(["28", "4002", "14"], True) == ["28", "40", "02", "14"]

    def test_relaxed_pads_single_digit(self):
        assert expand_number_tokens(["48", "46", "1"], True) == ["48", "46", "01"]

    def test_relaxed_rejects_glued_whose_halves_are_out_of_range(self):
        # 50 不是合法号码，不能硬拆成 50/02。
        assert expand_number_tokens(["28", "5002", "14"], True) is None

    def test_relaxed_rejects_three_digit_token(self):
        assert expand_number_tokens(["123", "45", "67"], True) is None

    def test_relaxed_rejects_five_digit_token(self):
        assert expand_number_tokens(["12345", "45", "67"], True) is None

    def test_relaxed_rejects_zero_single_digit(self):
        # 0 不是合法号码，补成 00 也不能放行。
        assert expand_number_tokens(["0", "45", "67"], True) is None


class TestFindNumberGroupsRelaxed:
    def test_glued_line_yields_nothing_strict(self):
        assert find_number_groups("303期精杀十码：05.17.29.41.04.16.28.4002.14") == []

    def test_glued_line_yields_group_when_relaxed(self):
        groups = find_number_groups(
            "303期精杀十码：05.17.29.41.04.16.28.4002.14", relaxed=True
        )
        assert groups == [["05", "17", "29", "41", "04", "16", "28", "40", "02", "14"]]

    def test_single_digit_line_yields_group_when_relaxed(self):
        groups = find_number_groups("303期稳杀7码（10.40.14.24.48.46.1）开00准", relaxed=True)
        assert groups == [["10", "40", "14", "24", "48", "46", "01"]]


class TestNumberGroupsForCount:
    def test_normal_segment_untouched(self):
        segment = "303期:【绝杀十码】05.17.29.41.04.16.28.40.02.14开：0000对"
        assert number_groups_for_count(segment, 10) == [
            ["05", "17", "29", "41", "04", "16", "28", "40", "02", "14"]
        ]

    def test_relaxed_not_used_when_strict_already_has_the_count(self):
        segment = (
            "303期:【绝杀十码】05.17.29.41.04.16.28.40.02.14开：0000对\n"
            "302期:【绝杀十码】11.23.35.47.12.24.3602.25开：狗45对"
        )
        groups = number_groups_for_count(segment, 10)
        # 只保留严格切分得到的那一组，不能把第二段的粘连号码也拆出来凑数。
        assert groups == [["05", "17", "29", "41", "04", "16", "28", "40", "02", "14"]]

    def test_relaxed_used_when_strict_has_no_matching_count(self):
        segment = "303期精杀十码：05.17.29.41.04.16.28.4002.14"
        assert number_groups_for_count(segment, 10) == [
            ["05", "17", "29", "41", "04", "16", "28", "40", "02", "14"]
        ]

    def test_relaxed_result_still_must_match_count(self):
        segment = "303期精杀十码：05.17.29.41.04.16.28.4002.14"
        assert number_groups_for_count(segment, 9) == []

    def test_without_expected_count_relaxed_is_never_used(self):
        assert number_groups_for_count("303期精杀十码：05.17.29.41.04.16.28.4002.14") == []


class TestExtractIssueNumbersTolerance:
    def test_glued_separator_is_recovered_for_the_exact_issue(self):
        text = (
            "第303期：澳彩【精杀⑩码】官网：状元红01490553g.com\n"
            "作者:01490553g.com\n"
            "303期精杀十码：开000准\n"
            "05.17.29.41.04.16.28.4002.14\n"
            "302期精杀十码：开狗45准\n"
            "12.24.36.48.10.22.34.46.04.16\n"
        )
        found = extract_issue_numbers(
            text, ["303"], keywords=["精杀十码"], expected_count=10, anchor="作者:01490553g.com"
        )
        assert found == {
            "303": ["05", "17", "29", "41", "04", "16", "28", "40", "02", "14"]
        }

    def test_single_digit_is_recovered_for_the_exact_issue(self):
        text = (
            "303期：周公神算网【稳杀七码】已免费公开\n"
            "作者:火红太阳\n"
            "303期稳杀7码（10.40.14.24.48.46.1）开00准\n"
            "302期稳杀7码（40.33.03.10.21.43.25）开狗45准\n"
        )
        found = extract_issue_numbers(
            text, ["303"], keywords=["稳杀7码"], expected_count=7, anchor="作者:火红太阳"
        )
        assert found == {"303": ["10", "40", "14", "24", "48", "46", "01"]}

    def test_duplicate_numbers_are_still_rejected(self):
        # 容错切分不能把重号放行：36 出现两次仍判失败。
        text = (
            "303期:【绝杀十码】准准准\n"
            "耿耿在怀 发表于 04月27日 16:36:58\n"
            "303期:【绝杀十码】11.23.35.47.12.24.36.13.25.36开：0000对\n"
        )
        found = extract_issue_numbers(
            text, ["303"], keywords=["绝杀十码"], expected_count=10, anchor="耿耿在怀"
        )
        assert found == {}

    def test_missing_issue_is_not_borrowed_from_neighbour(self):
        text = (
            "303期精杀十码：05.17.29.41.04.16.28.4002.14\n"
            "302期精杀十码：12.24.36.48.10.22.34.46.04.16\n"
        )
        found = extract_issue_numbers(text, ["304"], keywords=["精杀十码"], expected_count=10)
        assert found == {}
