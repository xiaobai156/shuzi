"""时间戳不能跨行借给号码组，也不能遮掉完整的本期号码。"""

import pytest

from kill_numbers.parsing.common import extract_issue_numbers, find_number_groups


NUMBERS = ["11", "12", "13", "14", "15", "16", "17", "18", "19", "20"]
NUMBER_LINE = " ".join(NUMBERS)


@pytest.mark.parametrize(
    "timestamp",
    [
        "09:00:00",
        "09:00:01",
        "09:00:09",
        "09:00:49",
        "09:00:50",
        "09:00:53",
        "09:00:59",
        "09:01",
        "9:05:03",
        "09：00：03",
        "09 : 00 : 03",
    ],
)
def test_timestamp_tail_never_joins_next_number_line(timestamp):
    text = f"发表于 10月27日 {timestamp}\n{NUMBER_LINE}"
    assert find_number_groups(text) == [NUMBERS]


@pytest.mark.parametrize("seconds", ["01", "49", "53"])
@pytest.mark.parametrize("region", [None, "top", "bottom"])
def test_complete_issue_with_timestamp_keeps_exact_numbers(seconds, region):
    text = f"300期 绝杀十码\n发表于 10月27日 09:00:{seconds}\n{NUMBER_LINE}"
    assert extract_issue_numbers(
        text,
        ["300"],
        keywords=["绝杀十码"],
        expected_count=10,
        strict_ambiguous=True,
        region=region,
    ) == {"300": NUMBERS}


@pytest.mark.parametrize("seconds", ["01", "49", "53"])
def test_timestamp_cannot_complete_nine_numbers(seconds):
    text = (
        f"300期 绝杀十码\n发表于 10月27日 09:00:{seconds}\n"
        + " ".join(NUMBERS[:-1])
    )
    assert extract_issue_numbers(
        text, ["300"], keywords=["绝杀十码"], expected_count=10
    ) == {}


@pytest.mark.parametrize("timestamp", ["09:00:03", "09:03", "09 : 00 : 03"])
def test_timestamp_hour_cannot_complete_nine_numbers(timestamp):
    text = "300期 绝杀十码\n" + " ".join(NUMBERS[:-1]) + f" {timestamp}"
    assert extract_issue_numbers(
        text, ["300"], keywords=["绝杀十码"], expected_count=10
    ) == {}


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_valid_numbers_can_still_wrap_across_lines(newline):
    text = (
        f"300期 绝杀十码{newline}发表于 10月27日 09:00:53{newline}"
        + " ".join(NUMBERS[:5])
        + newline
        + " ".join(NUMBERS[5:])
    )
    assert extract_issue_numbers(
        text, ["300"], keywords=["绝杀十码"], expected_count=10
    ) == {"300": NUMBERS}


@pytest.mark.parametrize("timestamp", ["09:00:03", "09：00：03", "09 : 00 : 03"])
def test_timestamp_is_a_boundary_between_partial_number_groups(timestamp):
    text = " ".join(NUMBERS[:5]) + f" {timestamp}\n" + " ".join(NUMBERS[5:])
    assert find_number_groups(text) == [NUMBERS[:5], NUMBERS[5:]]
    assert extract_issue_numbers(
        "300期 绝杀十码\n" + text,
        ["300"],
        keywords=["绝杀十码"],
        expected_count=10,
    ) == {}


@pytest.mark.parametrize("placement", ["before", "after"])
def test_timestamp_on_same_line_does_not_add_or_remove_numbers(placement):
    text = (
        f"09:00:03 {NUMBER_LINE}"
        if placement == "before"
        else f"{NUMBER_LINE} 09:00:03"
    )
    assert find_number_groups(text) == [NUMBERS]


@pytest.mark.parametrize("open_marker", ["", "开:0000准\n", "开:蛇14准\n"])
def test_timestamp_guard_applies_to_all_number_collection_paths(open_marker):
    text = f"300期 绝杀十码 {open_marker}发表于 10月27日 09:00:53\n{NUMBER_LINE}"
    assert extract_issue_numbers(
        text, ["300"], keywords=["绝杀十码"], expected_count=10
    ) == {"300": NUMBERS}


def test_eleven_real_numbers_are_not_trimmed_to_ten():
    text = f"300期 绝杀十码\n发表于 10月27日 09:00:53\n{NUMBER_LINE} 21"
    assert extract_issue_numbers(
        text, ["300"], keywords=["绝杀十码"], expected_count=10
    ) == {}


def test_timestamp_does_not_hide_same_issue_conflict():
    text = (
        f"300期 绝杀十码\n发表于 10月27日 09:00:53\n{NUMBER_LINE}\n"
        "300期 绝杀十码\n发表于 10月27日 09:00:01\n"
        "21 22 23 24 25 26 27 28 29 30"
    )
    with pytest.raises(ValueError, match="候选不唯一"):
        extract_issue_numbers(
            text,
            ["300"],
            keywords=["绝杀十码"],
            expected_count=10,
            strict_ambiguous=True,
        )


@pytest.mark.parametrize("issue", ["299", "301"])
def test_timestamp_does_not_lend_numbers_to_another_issue(issue):
    text = f"300期 绝杀十码\n发表于 10月27日 09:00:53\n{NUMBER_LINE}"
    assert extract_issue_numbers(
        text, [issue], keywords=["绝杀十码"], expected_count=10
    ) == {}
