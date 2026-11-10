"""澳门大佬标题缺「期」字的兼容回归（2026-10-20）。

293 期站方把标题从「293期：【绝杀⑩码】」改成「293：【绝杀⑩码】」，
_identity_title_match 旧正则强制要求「期」，导致专属解析直接报
「没有找到接口当前期绝杀十码标题」。修复后「期」与冒号都可接受，
其余契约（身份、数量 10、底部窗口）不变。
"""
import pytest

from kill_numbers.parsing.dedicated.site_parsers import _identity_title_match


def test_title_with_issue_marker_period():
    # 标题行不含站名，才能被当作标题（含站名的行会被跳过，用于避免把正文行当标题）
    lines = ["作者:澳门大佬", "293期：【绝杀⑩码】〓 稳定下注"]
    match = _identity_title_match(lines, "澳门大佬")
    assert match is not None
    assert match.group(1) == "293"


def test_title_without_period_marker_uses_colon():
    lines = ["293：【绝杀⑩码】〓 稳定下注", "作者:澳门大佬"]
    match = _identity_title_match(lines, "澳门大佬")
    assert match is not None
    assert match.group(1) == "293"


def test_title_identity_line_is_skipped():
    # 含站名的那行不算标题，必须靠真正标题行命中
    lines = ["澳门大佬", "293：【绝杀⑩码】"]
    match = _identity_title_match(lines, "澳门大佬")
    assert match is not None
    assert match.group(1) == "293"


def test_title_missing_ten_code_keyword_still_rejected():
    lines = ["293：【绝杀六码】", "作者:澳门大佬"]
    assert _identity_title_match(lines, "澳门大佬") is None


@pytest.mark.parametrize("marker", ["十", "10", "⑩"])
def test_title_accepts_all_ten_markers(marker):
    lines = [f"293：【绝杀{marker}码】"]
    match = _identity_title_match(lines, "澳门大佬")
    assert match is not None
    assert match.group(1) == "293"


def test_title_with_identity_suffix_is_accepted():
    # 312 期起「独占鳌头」「可歌可泣」把署名追加到标题末尾，
    # 标题行因此含站名；署名在期号之后属于真正的标题，必须接受。
    lines = ["作者:独占鳌头", "312期：【绝杀十码】〓 独占鳌头"]
    match = _identity_title_match(lines, "独占鳌头")
    assert match is not None
    assert match.group(1) == "312"


def test_title_with_identity_before_issue_marker_is_still_skipped():
    # 站名出现在期号之前的是页头/作者行，仍不能当标题。
    lines = ["独占鳌头 312期：【绝杀十码】"]
    assert _identity_title_match(lines, "独占鳌头") is None
