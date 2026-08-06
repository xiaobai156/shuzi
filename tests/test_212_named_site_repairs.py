import copy

import crawler
from kill_numbers.acquisition.strategies import admin_article


def target_by_name(name: str) -> dict:
    return next(target for target in crawler.load_targets() if target.get("name") == name)


def buke_212_fixture() -> str:
    return """频果报论坛准杀八码 212期
不可或缺 发表于 07月31日 10:37:48
208期:[准杀八码]12.04.42.10.36.11.43.20开鸡09准
209期:[准杀八码]06.19.08.05.36.38.25.16开蛇13准
210期:[准杀八码]34.21.46.44.29.33.09.16开兔15准
211期:[准杀八码]37.06.38.24.30.49.35.12开龙02准
212期:[准杀八码]43.31.33.48.32.23.45.46开鼠18准
365期:[准杀八码]28.24.49.11.35.30.02.25开鼠42准
001期:[准杀八码]01.02.03.04.05.06.07.08开鼠42准
上一篇:
"""


def buke_bottom_fixture() -> str:
    return """频果报论坛准杀八码 213期
不可或缺 发表于 08月01日 10:09:26
208期:[准杀八码]12.04.42.10.36.11.43.20开鸡09准
209期:[准杀八码]06.19.08.05.36.38.25.16开蛇13准
365期:[准杀八码]28.24.49.11.35.30.02.25开鼠42准
001期:[准杀八码]01.02.03.04.05.06.07.08开鼠42准
207期:[准杀八码]26.11.35.14.32.37.06.46开马19准
208期:[准杀八码]08.20.47.02.35.09.17.27开兔15准
209期:[准杀八码]23.27.08.30.02.14.09.19开蛇13准
210期:[准杀八码]05.38.10.21.36.09.24.47开马49准
211期:[准杀八码]12.22.08.47.19.39.36.41开马01准
212期:[准杀八码]49.22.31.10.27.07.25.17开牛06准
213期:[准杀八码]25.34.41.18.17.02.42.27开0000准
上一篇:
"""


def dream_bottom_fixture() -> str:
    return """213期:如梦如幻绝杀七码
如梦如幻 发表于 08月01日 03:32:09
001期：【绝杀七码】《01.02.03.04.05.06.07》
002期：【绝杀七码】《08.09.10.11.12.13.14》
211期：【绝杀七码】《15.16.17.18.19.20.21》
212期：【绝杀七码】《22.23.24.25.26.27.28》
213期：【绝杀七码】《29.30.31.32.33.34.35》
上一篇:
"""


def dance_floor_fixture() -> str:
    return """212期:舞榭歌楼→[绝杀 十 码]←专业研究
作者:舞榭歌楼
208期:绝杀10码《20.05.10.42.32.01.14.48.37.21》开鼠19对
209期:绝杀10码《31.13.45.24.27.11.41.07.09.35》开猪08对
210期:绝杀10码《46.43.41.21.04.12.14.15.06.29》开马49对
211期:绝杀10码《38.05.45.34.43.27.10.39.24.28》开马01对
212期:绝杀10码《30.23.16.07.45.27.20.38.22.24》开??对
"""


def liuhe_fixture() -> str:
    return """212期绝杀十码
199期:⚔️绝杀十码⚔️[21.15.23.18.22.46.47.20.17.01]开:羊36 对
200期:⚔️绝杀十码⚔️[42.04.35.33.34.24.13.16.23.10]开:龙39 对
202期:⚔️绝杀十码⚔️[05.03.36.33.16.31.47.14.18.07]开:兔40 对
205期:⚔️绝杀十码⚔️[23.38.05.10.17.24.11.27.32.29]开:龙03 对
206期:⚔️绝杀十码⚔️[46.04.40.36.23.09.12.42.39.07]开:猴47 对
207期:⚔️绝杀十码⚔️[28.49.15.40.34.01.05.16.43.14]开:鼠31 对
209期:⚔️绝杀十码⚔️[35.43.03.26.12.45.42.17.23.28]开:猪08 对
210期:⚔️绝杀十码⚔️[26.04.07.02.44.48.45.16.09.39]开:马49 对
211期:⚔️绝杀十码⚔️[24.30.44.22.49.47.18.31.41.39]开:马01 对
212期:⚔️绝杀十码⚔️[19.08.35.07.22.27.04.09.30.47]开:?00对
"""


def wind_fixture() -> str:
    return """精品料
212期风吹草动[经典绝杀10码]准
212期:[经典绝杀10码]开:0000准
14.21.12.44.39.15.45.32.26.25
211期:[经典绝杀10码]开:马01错
24.39.10.36.27.49.37.33.01.44
210期:[经典绝杀10码]开:马49准
06.32.30.19.23.45.03.05.26.35
"""


def mingji_fixture() -> str:
    return """搜 索
登 陆
注 册
212期:[绝杀⑩码]〓 妙手回春
作者:铭记于心
211期:《铭记于心》 💄绝杀⑩码💄开:马01准
[02.03.05.07.10.11.14.16.17.21]
212期:《铭记于心》 💄绝杀⑩码💄开:0000准
[05.07.08.09.10.15.16.17.19.20]
"""


def xinzhu_fixture() -> str:
    return """精英榜212期:[绝杀十码]已公开
温馨提示:为了提高网速,部分连错期数记录已删除
212期:绝杀十码:18.04.05.40.17.30.20.39.43.19:开0000准
211期:绝杀十码:26.40.48.24.27.12.17.09.45.29:开马01准
210期:绝杀十码:14.24.31.34.03.36.08.45.44.26:开马49准
上一篇:精英榜212期:[春夏秋冬]已公开
"""


def shita_fixture() -> str:
    return """大家发(绝杀10码)
最给力的资料,尽在大家发站点735558.com
212期:『师太』绝杀十码开:0000准
[03.08.09.13.22.37.40.44.45.48]
211期:『师太』绝杀十码开:马01错
[01.08.15.20.28.30.31.39.44.48]
210期:『师太』绝杀十码开:马49准
[08.20.32.44.09.21.33.45.10.22]
"""


def test_buke_current_cycle_honors_configured_five_row_window():
    target = copy.deepcopy(target_by_name("不可或缺"))
    target["region"] = "top"

    assert crawler.extract_top_article_history_current_cycle_numbers(
        buke_212_fixture(), ["212"], target
    ) == {
        "212": ["43", "31", "33", "48", "32", "23", "45", "46"]
    } 


def test_buke_bottom_region_selects_current_cycle_tail():
    target = target_by_name("不可或缺")

    assert target["region"] == "bottom"
    assert crawler.extract_top_article_history_current_cycle_numbers(
        buke_bottom_fixture(), ["213"], target
    ) == {
        "213": ["25", "34", "41", "18", "17", "02", "42", "27"]
    }


def test_dream_bottom_region_selects_history_tail():
    target = target_by_name("如梦如幻杀七码")

    assert target["region"] == "bottom"
    assert crawler.parse_target_content(dream_bottom_fixture(), target, ["213"]) == {
        "213": ["29", "30", "31", "32", "33", "34", "35"]
    }


def test_article_history_honors_named_site_five_row_window():
    target = target_by_name("舞榭歌楼")

    assert crawler.parse_target_content(dance_floor_fixture(), target, ["212"]) == {
        "212": ["30", "23", "16", "07", "45", "27", "20", "38", "22", "24"]
    }


def test_liuhe_dedicated_parser_keeps_data_document_boundary():
    target = {
        "name": "六合绝杀十码",
        "keywords": ["绝杀10码", "绝杀十码"],
        "count": 10,
        "region": "bottom",
        "anchor": "六合公式-唯一官网",
        "issue_position_window": 5,
        "special_parser": "liuhe_bottom_10",
    }

    assert crawler.parse_target_content(liuhe_fixture(), target, ["212"]) == {
        "212": ["19", "08", "35", "07", "22", "27", "04", "09", "30", "47"]
    }
    assert crawler.parse_target_content(liuhe_fixture(), target, ["999"]) == {}


def test_fengchuicaodong_uses_the_observed_top_data_order():
    target = copy.deepcopy(target_by_name("风吹草动"))

    assert target["region"] == "top"
    assert crawler.parse_target_content(wind_fixture(), target, ["212"]) == {
        "212": ["14", "21", "12", "44", "39", "15", "45", "32", "26", "25"]
    }


def test_mingji_accepts_navigation_prefix_and_circled_ten_code():
    target = target_by_name("铭记于心")

    assert crawler.extract_identity_article_bottom_10_numbers(
        mingji_fixture(), ["212"], target
    ) == {
        "212": ["05", "07", "08", "09", "10", "15", "16", "17", "19", "20"]
    } 


def test_mingji_api_title_accepts_circled_ten_code():
    target = target_by_name("铭记于心")

    admin_article.validate_identity_article_api_data(
        {"authorNickname": "铭记于心", "title": "212期：【绝杀⑩码】〓 妙手回春"},
        target,
    )


def test_xinzhu_uses_exact_section_heading_when_footer_anchor_is_elsewhere():
    target = target_by_name("新竹论坛")

    assert crawler.parse_target_content(xinzhu_fixture(), target, ["212"]) == {
        "212": ["18", "04", "05", "40", "17", "30", "20", "39", "43", "19"]
    }


def test_shita_allows_end_of_document_after_strict_rows():
    target = target_by_name("师太")

    assert crawler.parse_target_content(shita_fixture(), target, ["212"]) == {
        "212": ["03", "08", "09", "13", "22", "37", "40", "44", "45", "48"]
    }
