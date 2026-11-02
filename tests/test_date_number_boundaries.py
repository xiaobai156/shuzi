"""日期尾数与号码组跨行粘连的回归（2026-10-28）。

背景：正文里出现完整日期（如 `2026-10-27`）且紧贴号码行时，日期末段 `27`
会被换行分隔符连进号码组，把 10 个号拼成 11 个，导致本期判失败；
若真实号码只有 9 个，还可能被日期尾数凑成 10 个而产出错值。
修法与时间戳一致：把完整日期遮蔽成非分隔符，使其无法参与号码拼接。
"""
import pytest

from kill_numbers.parsing.common import extract_issue_numbers, find_number_groups

NUMBERS = "01 02 03 04 05 06 07 08 09 19"
EXPECTED = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "19"]

DATE_FORMS = ["2026-10-27", "2026.10.27", "2026/10/27", "2026年10月27日"]


@pytest.mark.parametrize("date", DATE_FORMS)
def test_date_on_label_line_does_not_join_numbers(date):
    assert find_number_groups(f"298期 杀十码 {date}\n{NUMBERS}") == [EXPECTED]


@pytest.mark.parametrize("date", DATE_FORMS)
def test_date_on_its_own_line_does_not_join_numbers(date):
    assert find_number_groups(f"298期 杀十码\n{date}\n{NUMBERS}") == [EXPECTED]


@pytest.mark.parametrize("date", DATE_FORMS)
def test_date_with_time_does_not_join_numbers(date):
    assert find_number_groups(f"298期 杀十码 {date} 09:00:53\n{NUMBERS}") == [EXPECTED]


@pytest.mark.parametrize("date", DATE_FORMS)
def test_date_tail_cannot_complete_nine_numbers(date):
    """只有 9 个真实号码时，日期末段不得把它凑成 10 个。"""
    nine = "01 02 03 04 05 06 07 08 09"
    groups = find_number_groups(f"298期 杀十码 {date}\n{nine}")
    assert all(len(group) != 10 for group in groups)


@pytest.mark.parametrize("separator", ["-", ".", "/"])
def test_two_digit_number_groups_are_not_masked(separator):
    """号码组自身用 - . / 作分隔时必须照常识别，不能被日期规则吃掉。"""
    text = separator.join(EXPECTED)
    assert find_number_groups(f"298期 杀十码 {text}") == [EXPECTED]


@pytest.mark.parametrize("date", DATE_FORMS)
def test_full_issue_parse_with_date_keeps_exact_numbers(date):
    text = f"298期 杀十码 {date}\n{NUMBERS}\n297期 杀十码 11 12 13 14 15 16 17 18 19 20"
    got = extract_issue_numbers(text, ["298"], keywords=["杀十码"], expected_count=10)
    assert got == {"298": EXPECTED}


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_valid_numbers_still_wrap_across_lines_with_date(newline):
    body = newline.join(["01 02 03 04 05", "06 07 08 09 19"])
    assert find_number_groups(f"298期 杀十码 2026-10-27{newline}{body}") == [EXPECTED]


def test_date_masking_applies_to_open_placeholder_path():
    """占位路径（开:0000 之后取号码）同样要挡住日期粘连。"""
    text = f"298期 杀十码 开:0000准 2026-10-27\n{NUMBERS}"
    assert find_number_groups(text) == [EXPECTED]


def test_date_masking_applies_to_real_draw_path():
    """真实开奖号路径（只看换行之后）同样要挡住日期粘连。"""
    text = f"298期 杀十码 开:蛇14准 2026-10-27\n{NUMBERS}"
    assert find_number_groups(text) == [EXPECTED]
