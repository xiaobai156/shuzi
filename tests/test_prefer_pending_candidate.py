"""人在江湖类站点：同一期号被站点重复标注时的 opt-in 未开奖优先消歧。"""
import pytest

from kill_numbers.parsing.common import extract_issue_numbers, select_candidate

DUPLICATED = (
    "起点\n"
    "215期 栏目A 开:0000准\n01 02 03\n"
    "215期 栏目A 开:蛇14准\n04 05 06\n"
    "终点"
)


def parse(**kwargs):
    return extract_issue_numbers(
        DUPLICATED,
        ["215"],
        keywords=["栏目A"],
        expected_count=3,
        anchor="起点",
        stop_anchor="终点",
        strict_ambiguous=True,
        **kwargs,
    )


def test_duplicated_issue_still_stops_by_default():
    with pytest.raises(ValueError, match="候选不唯一"):
        parse()


def test_prefer_pending_picks_undrawn_row():
    assert parse(prefer_pending=True) == {"215": ["01", "02", "03"]}


def test_prefer_pending_stops_when_two_rows_are_pending():
    content = (
        "起点\n"
        "215期 栏目A 开:0000准\n01 02 03\n"
        "215期 栏目A 开:??准\n04 05 06\n"
        "终点"
    )
    with pytest.raises(ValueError, match="候选不唯一"):
        extract_issue_numbers(
            content,
            ["215"],
            keywords=["栏目A"],
            expected_count=3,
            anchor="起点",
            stop_anchor="终点",
            strict_ambiguous=True,
            prefer_pending=True,
        )


def test_prefer_pending_keeps_single_candidate_behaviour():
    content = "起点\n215期 栏目A 01 02 03\n终点"
    assert extract_issue_numbers(
        content,
        ["215"],
        keywords=["栏目A"],
        expected_count=3,
        anchor="起点",
        stop_anchor="终点",
        strict_ambiguous=True,
        prefer_pending=True,
    ) == {"215": ["01", "02", "03"]}


def test_select_candidate_default_is_unchanged():
    candidates = [
        (["01", "02", "03"], "215期 开:0000准"),
        (["04", "05", "06"], "215期 开:蛇14准"),
    ]
    with pytest.raises(ValueError, match="候选不唯一"):
        select_candidate(candidates, strict_ambiguous=True, issue="215")
    assert select_candidate(
        candidates, strict_ambiguous=True, issue="215", prefer_pending=True
    ) == ["01", "02", "03"]
