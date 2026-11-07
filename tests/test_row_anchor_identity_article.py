from copy import deepcopy

import crawler


def target_by_name(name: str) -> dict:
    return next(target for target in crawler.load_targets() if target.get("name") == name)


def article_fixture(issue: str = "310") -> str:
    return "\n".join(
        [
            "勤能补拙",
            f"{issue}期：【绝杀10码】 资料精品",
            "307期: 😍",
            "『勤能补拙』",
            "😍",
            "绝杀⑩码",
            "😍开:",
            "牛42",
            "准",
            "【17.02.33.29.05.14.45.38.26.41】",
            "308期: 🚣",
            "《横财发家》",
            "🚣",
            "绝杀⑩码",
            "🚣开:",
            "鸡46",
            "准",
            "【15.03.26.09.38.02.39.21.27.45】",
            "309期: 🚣",
            "《横财发家》",
            "🚣",
            "绝杀⑩码",
            "🚣开:",
            "马13",
            "准",
            "【39.29.38.27.03.15.14.17.41.02】",
            "310期: 🚣",
            "《横财发家》",
            "🚣",
            "绝杀⑩码",
            "🚣开:",
            "00",
            "准",
            "【41.05.21.46.29.10.33.22.34.17】",
        ]
    )


def test_qin_neng_bu_zhuo_config_uses_row_anchor_without_renaming():
    target = target_by_name("勤能补拙")
    assert target["name"] == "勤能补拙"
    assert target["anchor"] == "勤能补拙"
    assert target["article_identity"] == "勤能补拙"
    assert target["row_anchor"] == "横财发家"
    assert target["region"] == "bottom"
    assert target["count"] == 10


def test_row_anchor_captures_rows_published_under_other_signature():
    target = target_by_name("勤能补拙")
    assert crawler.extract_identity_article_bottom_10_numbers(
        article_fixture(),
        ["310"],
        target,
    ) == {"310": ["41", "05", "21", "46", "29", "10", "33", "22", "34", "17"]}


def test_row_anchor_still_requires_original_article_identity():
    target = deepcopy(target_by_name("勤能补拙"))
    target["article_identity"] = "别人"
    try:
        crawler.extract_identity_article_bottom_10_numbers(
            article_fixture(),
            ["310"],
            target,
        )
    except ValueError as exc:
        assert "接口作者身份不匹配" in str(exc)
    else:
        raise AssertionError("作者身份不符未被拒绝")


def test_without_row_anchor_other_signature_rows_stay_unused():
    target = deepcopy(target_by_name("勤能补拙"))
    target.pop("row_anchor", None)
    assert crawler.extract_identity_article_bottom_10_numbers(
        article_fixture(),
        ["310"],
        target,
    ) == {}


def mixed_signature_fixture(issue: str = "310") -> str:
    """真实 18:23 那份 310 页的形态：307 期及更早署名『勤能补拙』，308 期起改《横财发家》。"""
    return "\n".join(
        [
            "勤能补拙",
            f"{issue}期：【绝杀10码】 资料精品",
            "305期: 😍",
            "『勤能补拙』",
            "😍",
            "绝杀⑩码",
            "😍开:",
            "兔28",
            "准",
            "【36.38.37.12.01.02.49.48.24.25】",
            "308期: 🚣",
            "《横财发家》",
            "🚣",
            "绝杀⑩码",
            "🚣开:",
            "鸡46",
            "准",
            "【15.03.26.09.38.02.39.21.27.45】",
            "309期: 🚣",
            "《横财发家》",
            "🚣",
            "绝杀⑩码",
            "🚣开:",
            "马13",
            "准",
            "【39.29.38.27.03.15.14.17.41.02】",
            f"{issue}期: 🚣",
            "《横财发家》",
            "🚣",
            "绝杀⑩码",
            "🚣开:",
            "00",
            "准",
            "【41.05.21.46.29.10.33.22.34.17】",
        ]
    )


def test_row_anchor_falls_back_to_author_signature_for_older_issues():
    target = target_by_name("勤能补拙")
    found = crawler.extract_identity_article_bottom_10_numbers(
        mixed_signature_fixture(),
        ["305", "310"],
        target,
    )
    assert found["310"] == ["41", "05", "21", "46", "29", "10", "33", "22", "34", "17"]
    assert found["305"] == ["36", "38", "37", "12", "01", "02", "49", "48", "24", "25"]


def row_with_issue(issue: str, signature: str, numbers: str) -> list[str]:
    return [
        f"{issue}期: 🚣",
        signature,
        "🚣",
        "绝杀⑩码",
        "🚣开:",
        "00",
        "准",
        numbers,
    ]


def long_history_fixture(
    ambiguous_issue: str = "300",
    *,
    first_issue: int = 301,
    last_issue: int = 310,
) -> str:
    """按给定区间造历史行；两署名歧义行放在 ambiguous_issue 期。"""
    lines = ["勤能补拙", "310期：【绝杀10码】 资料精品"]
    issues = [str(issue) for issue in range(first_issue, last_issue + 1)]
    if ambiguous_issue not in issues:
        lines.extend(
            row_with_issue(
                ambiguous_issue,
                "《横财发家》『勤能补拙』",
                "【01.02.03.04.05.06.07.08.09.10】",
            )
        )
    for issue in range(first_issue, last_issue + 1):
        numbers = ".".join(
            f"{((issue * 3 + index * 4) % 49) + 1:02d}" for index in range(10)
        )
        signature = (
            "《横财发家》『勤能补拙』"
            if str(issue) == ambiguous_issue
            else "《横财发家》"
        )
        lines.extend(row_with_issue(str(issue), signature, f"【{numbers}】"))
    return "\n".join(lines)


def test_ambiguous_row_outside_window_does_not_kill_current_issue():
    target = target_by_name("勤能补拙")
    found = crawler.extract_identity_article_bottom_10_numbers(
        long_history_fixture("300"),
        ["310"],
        target,
    )
    assert list(found) == ["310"]


def test_ambiguous_row_for_requested_issue_still_rejected():
    target = target_by_name("勤能补拙")
    try:
        crawler.extract_identity_article_bottom_10_numbers(
            long_history_fixture("300"),
            ["300"],
            target,
        )
    except ValueError as exc:
        assert "同一行同时出现行标识" in str(exc)
    else:
        raise AssertionError("请求期本身的歧义行未被拒绝")


def test_row_with_both_signatures_is_rejected_not_guessed():
    target = target_by_name("勤能补拙")
    page = mixed_signature_fixture().replace(
        "310期: 🚣",
        "310期: 🚣『勤能补拙』",
    )
    try:
        crawler.extract_identity_article_bottom_10_numbers(page, ["310"], target)
    except ValueError as exc:
        assert "同一行同时出现行标识" in str(exc)
    else:
        raise AssertionError("同一行两种署名未被拒绝")


def test_ambiguous_row_inside_window_is_rejected_even_if_not_requested():
    target = target_by_name("勤能补拙")
    try:
        crawler.extract_identity_article_bottom_10_numbers(
            long_history_fixture("305", first_issue=301, last_issue=312),
            ["310"],
            target,
        )
    except ValueError as exc:
        assert "305期" in str(exc)
    else:
        raise AssertionError("窗口内的歧义行未被拒绝")


def test_ambiguous_row_does_not_consume_a_window_slot():
    """歧义行只参与判定，不能把窗口里最老的一条有效行挤出去。"""
    target = target_by_name("勤能补拙")
    found = crawler.extract_identity_article_bottom_10_numbers(
        long_history_fixture("300", first_issue=301, last_issue=310),
        ["301", "310"],
        target,
    )
    assert set(found) == {"301", "310"}


def test_row_anchor_beyond_window_position_is_skipped_not_guessed():
    target = target_by_name("勤能补拙")
    padding = "说明文字" * 12  # 去掉标点后 48 个字符，超出 40 字符行首窗口
    page = "\n".join(
        [
            "勤能补拙",
            "310期：【绝杀10码】 资料精品",
            f"310期: 🚣{padding}《横财发家》",
            "🚣",
            "绝杀⑩码",
            "🚣开:",
            "00",
            "准",
            "【41.05.21.46.29.10.33.22.34.17】",
        ]
    )
    assert crawler.extract_identity_article_bottom_10_numbers(page, ["310"], target) == {}

