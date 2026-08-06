import json
from pathlib import Path

import pytest

import crawler
from kill_numbers.parsing.dedicated.site_parsers import (
    dedicated_ten_row_candidates,
    extract_dedicated_ten_numbers,
)


URL = "https://5ipaulfv56.1081085bbs1.shop/bbs/topic.php?id=2911"


def shizhiminggui_target() -> dict:
    return {
        "url": URL,
        "name": "实至名归",
        "keywords": ["绝杀十码"],
        "count": 10,
        "region": "bottom",
        "anchor": "实至名归[绝杀十码]共同致富",
        "stop_anchor": "下一贴:",
        "issue_position_window": 10,
        "special_parser": "shizhiminggui_bottom_10",
    }


def shizhiminggui_fixture() -> str:
    return """214期:实至名归[绝杀十码]共同致富
作者:实至名归
205期: 🚵‍♀️『实至名归』 🚵‍♀️绝杀十码🚵‍♀️开:龙03错
【03.04.06.19.23.28.29.33.35.45】
206期: 🚵‍♀️『实至名归』 🚵‍♀️绝杀十码🚵‍♀️开:猴47准
【05.06.08.09.14.22.24.33.35.37】
207期: 🚵‍♀️『实至名归』 🚵‍♀️绝杀十码🚵‍♀️开:鼠31错
【18.21.27.31.34.36.39.41.42.45】
208期: 🚵‍♀️『实至名归』 🚵‍♀️绝杀十码🚵‍♀️开:鼠19准
【13.15.16.29.33.34.35.43.47.48】
209期: 🚵‍♀️『实至名归』 🚵‍♀️绝杀十码🚵‍♀️开:猪08准
【06.11.12.17.18.20.27.33.37.49】
210期: 🚵‍♀️『实至名归』 🚵‍♀️绝杀十码🚵‍♀️开:马49准
【06.13.14.17.18.23.24.27.29.32】
211期: 🚵‍♀️『实至名归』 🚵‍♀️绝杀十码🚵‍♀️开:马01准
【05.09.12.19.22.25.28.32.40.44】
212期: 🚵‍♀️『实至名归』 🚵‍♀️绝杀十码🚵‍♀️开:牛06准
【04.12.14.16.18.19.25.38.40.47】
213期: 🚵‍♀️『实至名归』 🚵‍♀️绝杀十码🚵‍♀️开:猴35准
【04.06.09.13.16.19.20.33.36.43】
214期: 🚵‍♀️『实至名归』 🚵‍♀️绝杀十码🚵‍♀️开:00准
【01.08.11.21.24.30.31.35.36.45】
下一贴:214期:生根落叶[二十四码]稳定下注
"""


def test_shizhiminggui_bottom10_uses_one_bounded_section():
    target = shizhiminggui_target()

    candidates = dedicated_ten_row_candidates(
        shizhiminggui_fixture(), target, "shizhiminggui_bottom_10"
    )

    assert list(candidates) == [
        "205",
        "206",
        "207",
        "208",
        "209",
        "210",
        "211",
        "212",
        "213",
        "214",
    ]
    assert extract_dedicated_ten_numbers(
        shizhiminggui_fixture(), ["205", "214"], target, "shizhiminggui_bottom_10"
    ) == {
        "205": ["03", "04", "06", "19", "23", "28", "29", "33", "35", "45"],
        "214": ["01", "08", "11", "21", "24", "30", "31", "35", "36", "45"],
    }


def test_shizhiminggui_bottom10_rejects_issue_outside_direction_window():
    assert extract_dedicated_ten_numbers(
        shizhiminggui_fixture(), ["204"], shizhiminggui_target(), "shizhiminggui_bottom_10"
    ) == {}


def test_shizhiminggui_bottom10_rejects_same_issue_conflict():
    conflicting = shizhiminggui_fixture().replace(
        "214期: 🚵‍♀️『实至名归』",
        "214期: 🚵‍♀️『实至名归』 🚵‍♀️绝杀十码🚵‍♀️开:00准\n【02.08.11.21.24.30.31.35.36.45】\n"
        "214期: 🚵‍♀️『实至名归』",
        1,
    )

    with pytest.raises(ValueError, match="214期 候选不唯一"):
        extract_dedicated_ten_numbers(
            conflicting, ["214"], shizhiminggui_target(), "shizhiminggui_bottom_10"
        )


def test_shizhiminggui_target_is_registered_in_project_config():
    targets = json.loads(
        Path(crawler.TARGETS_FILE).read_text(encoding="utf-8")
    )
    matches = [target for target in targets if target.get("url") == URL]

    assert len(matches) == 1
    target = matches[0]
    assert target["name"] == "实至名归"
    assert target["region"] == "bottom"
    assert target["count"] == 10
    assert target["issue_position_window"] == 10
    assert target["special_parser"] == "shizhiminggui_bottom_10"
