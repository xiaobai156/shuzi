"""站方把期数标签的「期」误写成形近字「棋」时的兼容（心旷神怡 278 期）。

只放宽期数标签本身这一个字符，其余行为（关键词、号码分组、歧义判定）不变。
"""
from kill_numbers.parsing.common import (
    extract_issue_numbers,
    iter_all_issue_segment_matches,
)

PAGE = (
    "271期【绝杀⑩码】【06.18.21.29.31.33.34.39.46.47】开猴33准\n"
    "277期【绝杀⑩码】【02.04.07.13.16.28.35.37.43.47】开猴11准\n"
    "278棋【绝杀⑩码】【01.11.13.14.15.25.34.37.47.49】开0000准\n"
)
EXPECTED = ["01", "11", "13", "14", "15", "25", "34", "37", "47", "49"]


def parse(text=PAGE, issue="278"):
    return extract_issue_numbers(
        text,
        [issue],
        keywords=["绝杀10码"],
        expected_count=10,
        position="first",
        strict_ambiguous=True,
    )


def test_qizi_marker_is_accepted():
    assert parse() == {"278": EXPECTED}


def test_standard_qi_marker_is_unchanged():
    text = "278期【绝杀⑩码】【01.11.13.14.15.25.34.37.47.49】开0000准"
    assert parse(text) == {"278": EXPECTED}


def test_qizi_row_is_its_own_segment():
    issues = [match.group(1) for match in iter_all_issue_segment_matches(PAGE)]
    assert ["277", "278"] == issues[-2:]


def test_qizi_without_number_group_is_ignored():
    assert parse("278棋牌室 欢迎光临\n") == {}
