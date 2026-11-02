"""跨期借值回归用例（占位符剥离 / 期号标签写法 / 成功TXT清空防护）。

对应 2026-10-28 复审指出的问题，锁死行为避免回退。
"""
import pytest

from kill_numbers.parsing.common import (
    extract_issue_numbers,
    find_number_groups,
    issue_segment_matches,
    iter_all_issue_segment_matches,
    segment_number_blocks,
)

NUMBERS = "05 11 12 13 14 15 16 17 18 19"
EXPECTED = ["05", "11", "12", "13", "14", "15", "16", "17", "18", "19"]


# ---------- 占位符剥离：8 种写法都必须取到本期号码 ----------
@pytest.mark.parametrize("placeholder", ["0000", "??", "00", "?00", "？00", "?0", "0", "000000"])
def test_pending_placeholder_same_line_keeps_numbers(placeholder):
    groups = find_number_groups(f"299期 绝杀十码 开:{placeholder} {NUMBERS}")
    assert groups and groups[0] == EXPECTED


@pytest.mark.parametrize("placeholder", ["0000", "??", "00", "?00", "？00", "?0", "0", "000000"])
def test_pending_placeholder_next_line_keeps_numbers(placeholder):
    groups = find_number_groups(f"299期 绝杀十码 开:{placeholder}\n{NUMBERS}")
    assert groups and groups[0] == EXPECTED


# ---------- 开奖号防线不能因为剥离而放松 ----------
def test_real_drawn_numbers_same_line_still_rejected():
    assert find_number_groups("298期 绝杀十码 开:05 12 33 44 49 01 02 03 04 06准") == []


def test_real_drawn_numbers_next_line_still_accepted():
    assert find_number_groups("215期 栏目A 开:蛇14准\n04 05 06") == [["04", "05", "06"]]


def test_numbers_before_open_marker_unaffected():
    assert find_number_groups(
        "299期 绝杀十码 [01.02.03.04.05.06.07.08.09.10] 开:0000准"
    ) == [["01", "02", "03", "04", "05", "06", "07", "08", "09", "10"]]


# ---------- 期号标签写法：漏「期」、4 位、行首伪标签都不得借下一期 ----------
@pytest.mark.parametrize(
    "next_label",
    [
        "299: ",
        "299 ：",
        "299期 ",
        "1298期 ",
        "1298: ",
        "12987期 ",
        "123456期 ",
        "第299期 ",
        "299期号 ",
        "09:30 开奖 ",
        "1: ",
    ],
)
def test_next_issue_label_does_not_lend_numbers(next_label):
    text = (
        "298期 绝杀十码 开:0000准\n"
        "01 02 03 04 05 06 07 08 09 19\n"
        f"{next_label}20 21 22 23 24 25 26 27 28 29\n"
    )
    got = extract_issue_numbers(text, ["298"], keywords=["绝杀十码"], expected_count=10)
    assert got.get("298") == ["01", "02", "03", "04", "05", "06", "07", "08", "09", "19"]


@pytest.mark.parametrize("next_label", ["1298: ", "12987期 "])
def test_long_next_issue_labels_do_not_lend_numbers(next_label):
    text = (
        "300期:杀十码\n"
        "11 12 13 14 15 16 17 18 19 20\n"
        f"{next_label}21 22 23 24 25 26 27 28 29 30\n"
    )
    got = extract_issue_numbers(text, ["300"], keywords=["杀十码"], expected_count=10)
    assert got == {"300": ["11", "12", "13", "14", "15", "16", "17", "18", "19", "20"]}


@pytest.mark.parametrize("row_label", ["09:30 开奖", "1:"])
def test_line_start_numeric_colon_fails_closed(row_label):
    text = f"298期 绝杀十码\n{row_label}\n01 02 03 04 05 06 07 08 09 19"
    got = extract_issue_numbers(text, ["298"], keywords=["绝杀十码"], expected_count=10)
    assert got == {}


def test_inline_timestamp_is_not_a_row_boundary():
    text = (
        "298期 绝杀十码\n"
        "发表于 10月27日 09:00:53\n"
        "01 02 03 04 05 06 07 08 09 19"
    )
    match = issue_segment_matches(text, "298")[0]
    label_end = match.start("body") - match.start()
    assert "01 02 03 04 05 06 07 08 09 19" in segment_number_blocks(
        match.group(0), label_end, "298"
    )[-1]


def test_overlong_period_labels_are_boundaries_but_not_captured_issues():
    text = "300期 杀十码\n11 12 13\n12987期 21 22 23"
    matches = list(iter_all_issue_segment_matches(text))
    assert [match.group(1) for match in matches] == ["300"]


def test_normal_two_issues_still_isolated():
    text = (
        "298期 绝杀十码 开:0000准\n"
        "01 02 03 04 05 06 07 08 09 19\n"
        "299期 20 21 22 23 24 25 26 27 28 29\n"
    )
    got = extract_issue_numbers(text, ["298"], keywords=["绝杀十码"], expected_count=10)
    assert got.get("298") == ["01", "02", "03", "04", "05", "06", "07", "08", "09", "19"]


def test_missing_issue_returns_empty_not_neighbour():
    text = (
        "298期 绝杀十码 开:0000准\n"
        "01 02 03 04 05 06 07 08 09 19\n"
        "299期 20 21 22 23 24 25 26 27 28 29\n"
    )
    got = extract_issue_numbers(text, ["300"], keywords=["绝杀十码"], expected_count=10)
    assert got == {}
