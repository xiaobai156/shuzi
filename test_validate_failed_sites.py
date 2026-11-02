import copy

import pytest

import crawler
import validate_failed_sites as validator


def load_fixture_content() -> str:
    return """频果报论坛准杀八码 209期
不可或缺 发表于 07月28日 09:59:19
208期:[准杀八码]12.04.42.10.36.11.43.20开鸡09准
209期:[准杀八码]06.19.08.05.36.38.25.16开蛇13准
365期:[准杀八码]28.24.49.11.35.30.02.25开鼠42准
001期:[准杀八码]01.02.03.04.05.06.07.08开鼠42准
207期:[准杀八码]26.11.35.14.32.37.06.46开马19准
208期:[准杀八码]08.20.47.02.35.09.17.27开兔15准
209期:[准杀八码]23.27.08.30.02.14.09.19开蛇13准
上一篇:
"""


def load_top_target() -> dict:
    target = copy.deepcopy(
        next(target for target in crawler.load_targets() if target.get("name") == "不可或缺")
    )
    target["region"] = "top"
    return target


def load_current_cycle_only_content() -> str:
    return """频果报论坛准杀八码 237期
不可或缺 发表于 08月25日 09:49:10
235期：【准杀八码】22.21.25.35.10.33.02.17开猪32准
236期：【准杀八码】17.26.31.46.11.24.23.48开猴11错
237期：【准杀八码】21.03.09.31.49.19.10.15开0000准
上一篇：
"""


def load_bottom_target() -> dict:
    return copy.deepcopy(
        next(target for target in crawler.load_targets() if target.get("name") == "不可或缺")
    )


def test_explicit_validation_names_do_not_expand_to_historical_failure_list():
    assert validator.validation_names(["作茧自缚"]) == ["作茧自缚"]


def test_current_cycle_wins_when_issue_repeats_in_legacy_cycle():
    found = validator.extract_buke_article_history_numbers(
        load_fixture_content(), ["208"], load_top_target()
    )

    assert found == {"208": ["12", "04", "42", "10", "36", "11", "43", "20"]}


def test_isolated_parser_accepts_current_cycle_without_legacy_seam():
    found = validator.extract_buke_article_history_numbers(
        load_current_cycle_only_content(), ["235", "236", "237"], load_bottom_target()
    )

    assert found == {
        "235": ["22", "21", "25", "35", "10", "33", "02", "17"],
        "236": ["17", "26", "31", "46", "11", "24", "23", "48"],
        "237": ["21", "03", "09", "31", "49", "19", "10", "15"],
    }


def test_formal_parser_accepts_current_cycle_without_legacy_seam():
    assert crawler.extract_top_article_history_current_cycle_numbers(
        load_current_cycle_only_content(), ["235", "236", "237"], load_bottom_target()
    ) == {
        "235": ["22", "21", "25", "35", "10", "33", "02", "17"],
        "236": ["17", "26", "31", "46", "11", "24", "23", "48"],
        "237": ["21", "03", "09", "31", "49", "19", "10", "15"],
    }


def test_isolated_parser_does_not_invent_missing_current_cycle_issue():
    assert validator.extract_buke_article_history_numbers(
        load_current_cycle_only_content(), ["238"], load_bottom_target()
    ) == {}


def test_isolated_parser_rejects_title_issue_outside_current_rows():
    content = load_current_cycle_only_content().replace("237期\n", "238期\n", 1)

    with pytest.raises(ValueError, match="当前文章标题期数 238 不在底部当前周期窗口内"):
        validator.extract_buke_article_history_numbers(
            content, ["237"], load_bottom_target()
        )


def test_isolated_parser_rejects_conflicting_current_cycle_rows():
    content = load_current_cycle_only_content().replace(
        "237期：【准杀八码】",
        "237期：【准杀八码】01.02.03.04.05.06.07.08开0000准\n"
        "237期：【准杀八码】",
    )

    with pytest.raises(ValueError, match="237期 候选不唯一"):
        validator.extract_buke_article_history_numbers(
            content, ["237"], load_bottom_target()
        )


def test_top_cycle_handles_title_issue_after_first_body_row():
    found = validator.extract_buke_article_history_numbers(
        load_fixture_content(), ["209"], load_top_target()
    )

    assert found == {"209": ["06", "19", "08", "05", "36", "38", "25", "16"]}


def test_formal_parser_matches_isolated_parser_for_cycle_fixture():
    content = load_fixture_content()
    target = load_top_target()

    assert crawler.extract_top_article_history_current_cycle_numbers(
        content, ["207", "208", "209"], target
    ) == {
        "208": ["12", "04", "42", "10", "36", "11", "43", "20"],
        "209": ["06", "19", "08", "05", "36", "38", "25", "16"],
    }


def test_formal_parser_missing_issue_is_not_invented():
    found = crawler.extract_top_article_history_current_cycle_numbers(
        load_fixture_content(), ["999"], load_top_target()
    )

    assert found == {}


def test_formal_parser_rejects_multiple_cycle_boundaries():
    content = load_fixture_content().replace(
        "上一篇:",
        "365期:[准杀八码]11.12.13.14.15.16.17.18开鼠42准\n"
        "001期:[准杀八码]21.22.23.24.25.26.27.28开鼠42准\n"
        "上一篇:",
    )

    with pytest.raises(ValueError, match="周期文档边界数量异常：2"):
        crawler.extract_top_article_history_current_cycle_numbers(
            content, ["209"], load_top_target()
        )


def test_neighbor_issues_stay_in_their_actual_cycle():
    found = validator.extract_buke_article_history_numbers(
        load_fixture_content(), ["207", "209"], load_top_target()
    )

    assert "207" not in found  # Only present in the legacy cycle.
    assert found["209"] == ["06", "19", "08", "05", "36", "38", "25", "16"]


def test_missing_issue_is_not_invented():
    found = validator.extract_buke_article_history_numbers(
        load_fixture_content(), ["999"], load_top_target()
    )

    assert found == {}


def test_conflict_inside_current_cycle_still_fails_closed():
    content = load_fixture_content()
    conflicting_row = "208期:[准杀八码]01.02.03.04.05.06.07.08开0000准\n"
    content = content.replace(
        "209期:[准杀八码]",
        conflicting_row + "209期:[准杀八码]",
        1,
    )

    with pytest.raises(ValueError, match="208期 候选不唯一"):
        validator.extract_buke_article_history_numbers(content, ["208"], load_top_target())

    with pytest.raises(ValueError, match="208期 候选不唯一"):
        crawler.extract_top_article_history_current_cycle_numbers(
            content, ["208"], load_top_target()
        )


def admin_target(name: str) -> dict:
    return next(target for target in crawler.load_targets() if target.get("name") == name)


def test_admin_identity_uses_only_bottom_ten_rows():
    target = admin_target("富甲一方")
    old_duplicate = "148期:『富甲一方』绝杀10码开:00准\n[01.01.02.03.04.05.06.07.08.09]\n"
    current_rows = "\n".join(
        f"{issue}期:『富甲一方』绝杀10码开:00准\n"
        f"[01.02.03.04.05.06.07.08.09.{number:02d}]"
        for issue, number in zip(range(202, 212), range(10, 20))
    )
    content = f"富甲一方\n211期:[绝杀10码]测试\n{old_duplicate}{current_rows}"

    found = validator.extract_admin_identity_bottom_numbers(content, ["211"], target)

    assert found == {
        "211": ["01", "02", "03", "04", "05", "06", "07", "08", "09", "19"]
    }
    assert crawler.extract_identity_article_bottom_10_numbers(
        content, ["211", "212"], target
    ) == {
        "211": ["01", "02", "03", "04", "05", "06", "07", "08", "09", "19"]
    }


def test_admin_identity_conflict_inside_bottom_window_fails_closed():
    target = admin_target("富甲一方")
    content = """富甲一方
211期:[绝杀10码]测试
210期:『富甲一方』绝杀10码开:00准
[01.02.03.04.05.06.07.08.09.10]
211期:『富甲一方』绝杀10码开:00准
[01.02.03.04.05.06.07.08.09.10]
[11.12.13.14.15.16.17.18.19.20]
"""

    with pytest.raises(ValueError, match="211期 候选不唯一"):
        validator.extract_admin_identity_bottom_numbers(content, ["211"], target)
    with pytest.raises(ValueError, match="211期 候选不唯一"):
        crawler.extract_identity_article_bottom_10_numbers(content, ["211"], target)


def test_mingji_placeholder_requires_title_and_previous_issue():
    target = admin_target("铭记于心")
    content = """铭记于心
211期:[绝杀10码]测试
210期:《铭记于心》绝杀10码开:马49准
[01.02.03.04.05.06.07.08.09.10]
水期:《铭记于心》绝杀10码开:0000准
[11.12.13.14.15.16.17.18.19.20]
"""

    found = validator.extract_admin_identity_bottom_numbers(content, ["211"], target)

    assert found == {
        "211": ["11", "12", "13", "14", "15", "16", "17", "18", "19", "20"]
    }
    assert crawler.extract_identity_article_bottom_10_numbers(
        content, ["211"], target
    ) == found


def test_shita_uses_next_document_marker_as_stop_boundary():
    target = admin_target("师太")
    content = """大家发(绝杀10码)
最给力的资料,尽在大家发站点735558.com
211期:『师太』绝杀十码开:0000准
[01.08.15.20.28.30.31.39.44.48]
210期:『师太』绝杀十码开:马49准
[08.20.32.44.09.21.33.45.10.22]
document.writeln("next document")
211期:『其他栏目』绝杀十码开:0000准
[02.03.04.05.06.07.08.09.10.11]
"""

    assert validator.extract_shita_consecutive_top_numbers(
        content, ["210", "211", "212"], target
    ) == {
        "210": ["08", "20", "32", "44", "09", "21", "33", "45", "10", "22"],
        "211": ["01", "08", "15", "20", "28", "30", "31", "39", "44", "48"],
    }
    assert crawler.extract_dedicated_ten_numbers(
        content, ["210", "211", "212"], target, "shita_top_10"
    ) == {
        "210": ["08", "20", "32", "44", "09", "21", "33", "45", "10", "22"],
        "211": ["01", "08", "15", "20", "28", "30", "31", "39", "44", "48"],
    }
