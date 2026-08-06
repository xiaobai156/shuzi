from copy import deepcopy

import crawler
from kill_numbers.acquisition.strategies.admin_article import (
    validate_identity_article_api_data,
)


EXPECTED_216 = {
    "幽冥战神": ["42", "35", "33", "18", "39", "41", "30", "31", "32", "20"],
    "横财发家": ["01", "02", "13", "14", "25", "26", "37", "38", "06", "18"],
    "潮牌天下": ["01", "13", "25", "37", "49", "02", "14", "26", "38", "06"],
    "五鬼抓马": ["35", "36", "37", "41", "42", "43", "45", "46", "47", "49"],
    "肝胆相渣": ["01", "02", "03", "14", "18", "22", "28", "29", "39", "42"],
}


def target_by_name(name: str) -> dict:
    return next(target for target in crawler.load_targets() if target.get("name") == name)


def identity_fixture(name: str, numbers: list[str]) -> str:
    generic = "01.02.03.04.05.06.07.08.09.10"
    rows = [
        f"206期:[{name}] 绝杀十码开:00准\n[{generic}]",
        *[
            f"{issue}期:[{name}] 绝杀十码开:00准\n[{generic}]"
            for issue in range(207, 216)
        ],
        f"216期:[{name}] 绝杀十码开:00准\n[{'.'.join(numbers)}]",
    ]
    return f"{name}\n216期:[绝杀十码] 当前\n" + "\n".join(rows)


def test_five_special_sites_are_registered_with_explicit_exception_scope():
    for name in EXPECTED_216:
        target = target_by_name(name)
        exception = target["onboarding_exception"]
        assert exception == {
            "type": "invalid_duplicate_baseline",
            "approved": True,
            "scope": "用户明确特例添加；仅放宽正式判重基线无效门禁",
            "validated_issues": ["216"],
        }
        assert target["count"] == 10

    top_target = target_by_name("幽冥战神")
    assert top_target["region"] == "top"
    assert top_target["anchor"] == "幽冥战神『杀特十码』"
    assert top_target["keywords"] == ["这十码淘汰"]

    for name in ("横财发家", "潮牌天下", "五鬼抓马", "肝胆相渣"):
        target = target_by_name(name)
        assert target["region"] == "bottom"
        assert target["special_parser"] == "identity_article_bottom_10"
        assert target["anchor"] == name == target["article_identity"]
        assert target["api_url"].endswith(target["url"].split("/article/manager/")[1].split("?")[0])


def test_identity_special_sites_extract_live_216_shape_and_exclude_206():
    for name in ("横财发家", "潮牌天下", "五鬼抓马", "肝胆相渣"):
        target = target_by_name(name)
        assert crawler.extract_identity_article_bottom_10_numbers(
            identity_fixture(name, EXPECTED_216[name]),
            ["206", "216"],
            target,
        ) == {"216": EXPECTED_216[name]}


def test_identity_special_sites_validate_article_identity_and_direction():
    for name in ("横财发家", "潮牌天下", "五鬼抓马", "肝胆相渣"):
        target = target_by_name(name)
        validate_identity_article_api_data(
            {"authorNickname": name, "title": "216期:[绝杀十码] 当前"},
            target,
        )

        wrong_direction = deepcopy(target)
        wrong_direction["region"] = "top"
        try:
            crawler.extract_identity_article_bottom_10_numbers(
                identity_fixture(name, EXPECTED_216[name]),
                ["216"],
                wrong_direction,
            )
        except ValueError as exc:
            assert "只允许 bottom" in str(exc)
        else:
            raise AssertionError(f"{name} 错方向未被拒绝")


def test_youming_top_anchor_extracts_216_and_rejects_missing_217():
    target = target_by_name("幽冥战神")
    content = """幽冥战神『杀特十码』
216期【这十码淘汰】开0000准
42.35.33.18.39.41.30.31.32.20
215期【这十码淘汰】开蛇14准
43.09.21.23.11.02.38.17.26.28
214期【这十码淘汰】开兔04准
05.20.16.07.27.41.37.23.17.47
213期【这十码淘汰】开猴35准
07.24.19.17.15.03.22.09.14.12
212期【这十码淘汰】开牛06准
02.24.37.07.39.27.09.25.26.38
211期【这十码淘汰】开马01准
08.11.18.20.07.27.02.09.43.39
"""

    assert crawler.parse_target_content(content, target, ["216"]) == {
        "216": EXPECTED_216["幽冥战神"]
    }
    assert crawler.parse_target_content(content, target, ["217"]) == {}

