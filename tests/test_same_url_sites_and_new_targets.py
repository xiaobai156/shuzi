import json
from pathlib import Path

import pytest

from check_duplicates import cache_site_key, validate_cache_data
from kill_numbers.parsing.registry import parse_generic


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TARGETS_PATH = PROJECT_ROOT / "targets.json"
FORBIDDEN_PATH = PROJECT_ROOT / "forbidden.json"
CACHE_PATH = PROJECT_ROOT / "recent_10_cache.json"

EXPECTED_NEW_TARGETS = {
    "赛玛荟独家": {
        "url": "https://4.48kk49.com:1888/Article/ar_content/id/135/tid/8.html",
        "anchor": "赛玛荟独家[绝杀十玛]",
        "keywords": ["绝杀"],
    },
    "金光大道": {
        "url": "https://4.48kk49.com:1888/Article/ar_content/id/33/tid/12.html",
        "anchor": "金光大道期期",
        "keywords": ["传真"],
    },
    "曾氏原创": {
        "url": "https://4.48kk49.com:1888/Article/ar_content/id/329/tid/22.html",
        "anchor": "香港曾氏原创[绝杀10碼]期期精准",
        "keywords": ["杀"],
    },
    "精选供参料": {
        "url": "https://4.48kk49.com:1888/Article/ar_content/id/369/tid/24.html",
        "anchor": "精选供参料╬◤原创杀10码实力◥已更新",
        "keywords": ["主杀"],
    },
    "彩坛至尊": {
        "url": "https://4.48kk49.com:1888/Article/ar_content/id/440/tid/29.html",
        "anchor": "彩坛至尊◤最新杀码◥已更新",
        "keywords": ["杀"],
    },
    "铁手": {
        "url": "https://4.48kk49.com:1888/Article/ar_content/id/544/tid/35.html",
        "anchor": "经典实战[杀10码]新铁算盘高手坛",
        "keywords": ["铁手杀10码"],
    },
    "百万翁": {
        "url": "https://4.48kk49.com:1888/Article/ar_content/id/563/tid/37.html",
        "anchor": "百万[翁→[独家◇绝杀10码]←已更新",
        "keywords": ["绝杀10码"],
    },
    "刘伯温第二": {
        "url": "https://4.48kk49.com:1888/Article/ar_content/id/638/tid/43.html",
        "anchor": "〖刘伯温〗→★[铁杀(十)码]★←已公开",
        "keywords": ["铁杀(十)码"],
    },
    "东方心经": {
        "url": "https://4.48kk49.com:1888/Article/ar_content/id/747/tid/52.html",
        "anchor": "东方心经☆准杀10码已上料",
        "keywords": ["心经准杀10码"],
    },
    "大刀皇": {
        "url": "https://4.48kk49.com:1888/Article/ar_content/id/767/tid/54.html",
        "anchor": "大刀皇☆[准杀十码]√",
        "keywords": ["准杀十码"],
    },
}

EXPECTED_ZHUYING_TARGET = {
    "url": "https://4.48kk49.com:1888/Article/ar_content/id/925/tid/71.html",
    "name": "竹影梅花",
    "article_id": "925",
    "keywords": ["绝杀10个数"],
    "count": 10,
    "region": "top",
    "anchor": "竹影梅花",
    "stop_anchor": "上一篇:",
    "issue_position_window": 10,
}


def test_cache_identity_uses_name_and_url_for_same_url_sites(tmp_path):
    same_url = "https://a.ttss.vip/list.aspx?id=79&page=1"
    assert cache_site_key("告别那时", same_url) != cache_site_key("私房一肖", same_url)

    data = {
        "version": 1,
        "generated_at": "2026-08-13T00:00:00",
        "recent_count": 10,
        "records": [
            {
                "name": "告别那时",
                "url": same_url,
                "issue": "218",
                "numbers": "01,02,03,04,05,06,07,08,09,10",
            },
            {
                "name": "私房一肖",
                "url": same_url,
                "issue": "218",
                "numbers": "11,12,13,14,15,16,17,18,19,20",
            },
        ],
        "failures": [],
    }

    assert validate_cache_data(data, tmp_path / "cache.json") == data["records"]


def test_same_url_targets_are_configured_as_distinct_named_sections():
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    same_url = "https://a.ttss.vip/list.aspx?id=79&page=1"
    matched = [item for item in targets if item.get("url") == same_url]
    by_name = {item.get("name"): item for item in matched}

    assert {"告别那时", "私房一肖"}.issubset(by_name)
    assert by_name["告别那时"]["anchor"] != by_name["私房一肖"]["anchor"]
    assert by_name["告别那时"]["article_identity"] != by_name["私房一肖"]["article_identity"]


@pytest.mark.parametrize("name,expected", EXPECTED_NEW_TARGETS.items())
def test_new_448kk49_targets_are_in_formal_config(name, expected):
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    matches = [item for item in targets if item.get("name") == name]

    assert len(matches) == 1
    target = matches[0]
    assert target["url"] == expected["url"]
    assert target["region"] == "top"
    assert target["count"] == 10
    assert target["anchor"] == expected["anchor"]
    assert target["keywords"] == expected["keywords"]
    assert target["stop_anchor"] == "上一篇:"
    assert target["issue_position_window"] == 10


def test_zhuying_meihua_target_is_configured_as_top_article():
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    matches = [item for item in targets if item.get("name") == "竹影梅花"]

    assert matches == [EXPECTED_ZHUYING_TARGET]


def test_zhuying_meihua_top_parser_selects_only_top_ten_rows():
    content = """
    竹影梅花
    225期 绝杀10个数 : [28,22,34,17,19,45,29,44,02,24]
    224期 绝杀10个数 : [36,37,01,22,04,21,17,46,43,27]
    223期 绝杀10个数 : [24,28,47,41,12,44,10,33,30,25]
    222期 绝杀10个数 : [08,37,04,39,38,03,01,10,11,05]
    221期 绝杀10个数 : [37,29,24,42,23,08,48,04,14,39]
    220期 绝杀10个数 : [01,26,33,08,45,37,07,25,22,17]
    218期 绝杀10个数 : [34,39,12,10,44,42,49,19,21,45]
    217期 绝杀10个数 : [11,13,28,19,34,36,42,07,17,39]
    216期 绝杀10个数 : [14,07,30,29,32,05,22,03,25,46]
    214期 绝杀10个数 : [14,24,17,32,01,12,13,20,06,30]
    213期 绝杀10个数 : [01,02,03,04,05,06,07,08,09,10]
    上一篇:
    213期 绝杀10个数 : [11,12,13,14,15,16,17,18,19,20]
    """

    found = parse_generic(content, ["225", "214"], EXPECTED_ZHUYING_TARGET)

    assert found["225"] == ["28", "22", "34", "17", "19", "45", "29", "44", "02", "24"]
    assert found["214"] == ["14", "24", "17", "32", "01", "12", "13", "20", "06", "30"]
    assert parse_generic(content, ["213"], EXPECTED_ZHUYING_TARGET) == {}


def test_reactivated_article_identity_uses_the_new_manager_url():
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    new_url = "https://zlkcyl.e6mlx-wj46o-rzcukg.work:29499/article/manager/6a0814c8e0d076537e1df7bd?url=hyl"
    old_url = "https://zlkcyl.e6mlx-wj46o-rzcukg.work:29499/article/admin/6a0814c8e0d076537e1df7bd?url=hyl"
    matches = [item for item in targets if item.get("url") == new_url]

    assert len(matches) == 1
    target = matches[0]
    assert target["name"] == "赢者为王"
    assert target["api_url"].endswith("/api/proxy/manager-articles/6a0814c8e0d076537e1df7bd")
    assert target["special_parser"] == "identity_article_bottom_10"
    assert target["article_identity"] == target["anchor"] == "赢者为王"
    assert target["region"] == "bottom"
    assert target.get("disabled") is not True
    assert not any(item.get("url") == old_url and not item.get("disabled") for item in targets)


def test_reactivated_topic_identity_uses_the_new_host():
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    new_url = "https://azjjosd.scyms-vek4g-yubibb.xyz:17455/topic/253943.html"
    matches = [item for item in targets if item.get("url") == new_url]

    assert len(matches) == 1
    target = matches[0]
    assert target["name"] == "拳王杀料"
    assert target["anchor"] == "拳王杀料"
    assert target["region"] == "top"
    assert target.get("disabled") is not True
    assert target.get("archive_tag") is None


def test_lianmeng_gaoshou_records_the_approved_nine_issue_exception():
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    url = "https://szfzml.mfuw8-bnsfa-gdaxhk.xyz/view.php?id=674"
    matches = [item for item in targets if item.get("url") == url]

    assert len(matches) == 1
    target = matches[0]
    assert target["name"] == "联盟高手"
    assert target["region"] == "bottom"
    assert target["count"] == 10
    exception = target["onboarding_exception"]
    assert exception["approved"] is True
    assert exception["validated_issue"] == "227"
    assert exception["valid_history_issues"] == ["227", "226", "225", "224", "223", "222", "221", "220", "219"]
    assert exception["missing_history_issues"] == ["218"]


def test_yuyu_qingqing_is_archived_as_a_bottom_kill_numbers_site():
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    url = "https://5ipaulfv56.1081085bbs1.shop/bbs/topic.php?id=3038"
    matches = [item for item in targets if item.get("url") == url]

    assert len(matches) == 1
    target = matches[0]
    assert target["name"] == "郁郁青青"
    assert target["keywords"] == ["绝杀十码", "绝杀10码"]
    assert target["count"] == 10
    assert target["region"] == "bottom"
    assert target["anchor"] == "郁郁青青"
    assert target["stop_anchor"] == "下一贴:"
    assert target["issue_position_window"] == 10
    assert target["disabled"] is True
    assert target["archive_tag"] == "用户指定封存"


@pytest.mark.parametrize(
    ("name", "url"),
    [
        (
            "福星金牌",
            "https://cahgjib.5blx9-z8506-ekiwxc.work:29488/article/admin/6a1439574346bc68aea4f09e?url=lqz",
        ),
        ("过往云烟", "https://hl.90216a.com/read.php?tid=526"),
    ],
)
def test_requested_238_anchor_failures_are_archived(name: str, url: str):
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    target = next(item for item in targets if item.get("name") == name)
    assert target["url"] == url
    assert target["disabled"] is True
    assert target["archive_tag"] == "用户指定封存"

    forbidden = json.loads(FORBIDDEN_PATH.read_text(encoding="utf-8"))["records"]
    assert any(
        item.get("name") == name
        and item.get("url") == url
        and item.get("status") == "archived"
        for item in forbidden
    )

    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    assert not any(
        item.get("name") == name and item.get("url") == url
        for item in cache.get("sites", [])
    )
    assert not any(
        item.get("name") == name and item.get("url") == url
        for item in cache.get("failures", [])
    )


@pytest.mark.parametrize(
    ("name", "url"),
    [
        (
            "港澳爆庄",
            "https://dgjdlk.0hrwo-8qjsc-spwvba.xyz:29411/article/admin/6a12e003c1d9e28d71e56fd5?url=cf",
        ),
        (
            "好料见证",
            "https://hcsuuoy.nimo7-9bgj4-fmspxt.xyz:29466/article/manager/6a4f2a8f4cd760c3a021ea24?url=cww",
        ),
    ],
)
def test_requested_239_failures_are_archived_and_removed_from_cache(name: str, url: str):
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    target = next(item for item in targets if item.get("name") == name)
    assert target["url"] == url
    assert target["disabled"] is True
    assert target["archive_tag"] == "用户指定封存"

    forbidden = json.loads(FORBIDDEN_PATH.read_text(encoding="utf-8"))["records"]
    assert any(
        item.get("name") == name
        and item.get("url") == url
        and item.get("status") == "archived"
        for item in forbidden
    )

    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    assert not any(
        item.get("name") == name and item.get("url") == url
        for item in cache.get("sites", [])
    )
    assert not any(
        item.get("name") == name and item.get("url") == url
        for item in cache.get("failures", [])
    )


def test_shiji_xingwang_is_archived_and_removed_from_cache():
    name = "世纪星王"
    url = "https://hl.www25195a.com/read.php?tid=678"
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    target = next(item for item in targets if item.get("name") == name)
    assert target["url"] == url
    assert target["disabled"] is True
    assert target["archive_tag"] == "用户指定封存"

    forbidden = json.loads(FORBIDDEN_PATH.read_text(encoding="utf-8"))["records"]
    assert any(
        item.get("name") == name
        and item.get("url") == url
        and item.get("status") == "archived"
        for item in forbidden
    )

    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    assert not any(item.get("name") == name for item in cache.get("sites", []))
    assert not any(item.get("name") == name for item in cache.get("failures", []))


@pytest.mark.parametrize(
    ("name", "url"),
    [
        (
            "金宝神皇",
            "https://uqdccnri.oy2bh-swrfr-gvzxkk.work:29499/article/admin/6a0322b356a1d26b7774455e?url=lcz",
        ),
        (
            "百年好事",
            "https://0130190808.345745.xyz/bbs/topic.php?id=20762",
        ),
        ("寿比南山", "https://szfzml.mfuw8-bnsfa-gdaxhk.xyz/view.php?id=412"),
        ("联盟高手", "https://szfzml.mfuw8-bnsfa-gdaxhk.xyz/view.php?id=674"),
        ("弦无虚发", "https://szfzml.mfuw8-bnsfa-gdaxhk.xyz/view.php?id=642"),
    ],
)
def test_requested_243_245_failures_are_archived_and_removed_from_cache(name: str, url: str):
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    target = next(item for item in targets if item.get("name") == name and item.get("url") == url)
    assert target["url"] == url
    assert target["disabled"] is True
    assert target["archive_tag"] == "用户指定封存"

    forbidden = json.loads(FORBIDDEN_PATH.read_text(encoding="utf-8"))["records"]
    assert any(
        item.get("name") == name
        and item.get("url") == url
        and item.get("status") == "archived"
        for item in forbidden
    )

    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    assert not any(
        item.get("name") == name and item.get("url") == url
        for item in cache.get("sites", [])
    )
    assert not any(
        item.get("name") == name and item.get("url") == url
        for item in cache.get("failures", [])
    )


def test_caijie_tianhua_is_configured_as_a_gb18030_bottom_kill_numbers_site():
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    url = "https://nlafoq9v.dh5565656.xyz/bbs/topic.php?id=1178"
    matches = [item for item in targets if item.get("url") == url]

    assert len(matches) == 1
    target = matches[0]
    assert target["name"] == "彩界天花"
    assert target["keywords"] == ["绝杀十码", "绝杀10码"]
    assert target["count"] == 10
    assert target["region"] == "bottom"
    assert target["anchor"] == "彩界天花"
    assert target["issue_position_window"] == 10
    assert target["encoding"] == "gb18030"
    assert target["disabled"] is True
    assert target["archive_tag"] == "用户指定封存"


def test_bainian_haoshi_is_configured_with_the_approved_history_exception():
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    url = "https://0130190808.345745.xyz/bbs/topic.php?id=20762"
    matches = [item for item in targets if item.get("url") == url]

    assert len(matches) == 1
    target = matches[0]
    assert target["name"] == "百年好事"
    assert target["keywords"] == ["绝杀十码", "绝杀10码"]
    assert target["count"] == 10
    assert target["region"] == "bottom"
    assert target["anchor"] == "百年好事"
    assert target["stop_anchor"] == "下一贴:"
    assert target["issue_position_window"] == 10
    exception = target["onboarding_exception"]
    assert exception["approved"] is True
    assert exception["type"] == "history_shortage"
    assert exception["validated_issue"] == "227"
    assert exception["valid_history_issues"] == [
        "227", "226", "225", "224", "223", "222", "221", "220", "219"
    ]
    assert exception["missing_history_issues"] == ["218"]
    assert target["disabled"] is True
    assert target["archive_tag"] == "用户指定封存"


def test_neimu_chuanzhen_is_configured_with_the_approved_cache_exception():
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    url = "https://0130190808.880646.xyz/bbs/topic.php?id=20693"
    matches = [item for item in targets if item.get("url") == url]

    assert len(matches) == 1
    target = matches[0]
    assert target["name"] == "内幕传真"
    assert target["keywords"] == ["绝杀十码", "绝杀10码"]
    assert target["count"] == 10
    assert target["region"] == "bottom"
    assert target["anchor"] == "内幕传真"
    assert target["stop_anchor"] == "下一贴:"
    assert target["issue_position_window"] == 10
    exception = target["onboarding_exception"]
    assert exception["approved"] is True
    assert exception["validated_issue"] == "238"
    assert exception["valid_history_issues"] == [
        "230", "231", "232", "233", "234", "235", "236", "237", "238", "239"
    ]
    assert exception["cache_failures_bypassed"] == [
        "六合绝杀十码 235期",
        "六合稳杀十码 235期",
        "六合稳杀七码 235期",
        "花仙子 235期",
    ]
    assert target.get("disabled") is not True


def test_anshen_limings_is_configured_as_a_bottom_kill_numbers_site():
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    url = "https://i97lk4jb16.333741bbs1.shop/bbs/topic.php?id=2868"
    matches = [item for item in targets if item.get("url") == url]

    assert len(matches) == 1
    target = matches[0]
    assert target["name"] == "安身立命"
    assert target["keywords"] == ["绝杀十码", "绝杀10码"]
    assert target["count"] == 10
    assert target["region"] == "bottom"
    assert target["anchor"] == "安身立命"
    assert target["content_class"] == "content"
    assert target.get("stop_anchor") is None
    assert target["issue_position_window"] == 10
    assert target["disabled"] is True
    assert target["archive_tag"] == "用户指定封存"

    forbidden = json.loads(FORBIDDEN_PATH.read_text(encoding="utf-8"))["records"]
    assert any(
        item.get("name") == "安身立命"
        and item.get("url") == url
        and item.get("status") == "archived"
        for item in forbidden
    )


def test_aomen_xinjing_is_configured_as_a_manager_identity_article():
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    url = "https://buzsxio.821n4-hgj04-edkrft.xyz:29455/article/manager/6a7ee5c7ee4739fbe9ea2714?url=bxj"
    matches = [item for item in targets if item.get("url") == url]

    assert len(matches) == 1
    target = matches[0]
    assert target["name"] == "澳门心经"
    assert target["api_url"].endswith("/api/proxy/manager-articles/6a7ee5c7ee4739fbe9ea2714")
    assert target["keywords"] == ["绝杀十码", "绝杀10码"]
    assert target["count"] == 10
    assert target["region"] == "bottom"
    assert target["anchor"] == target["article_identity"] == "澳门心经"
    assert target["issue_position_window"] == 10
    assert target["special_parser"] == "identity_article_bottom_10"
    # 2026-10-18 用户指定封存：291 期文章 API 404，文章已被站方删除
    assert target["disabled"] is True
    assert target["archive_tag"] == "用户指定封存"

    forbidden = json.loads(FORBIDDEN_PATH.read_text(encoding="utf-8"))["records"]
    assert any(
        item.get("name") == "澳门心经"
        and item.get("url") == url
        and item.get("status") == "archived"
        for item in forbidden
    )

    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    assert not any(
        item.get("name") == "澳门心经" and item.get("url") == url
        for item in cache.get("sites", [])
    )
    assert not any(
        item.get("name") == "澳门心经" and item.get("url") == url
        for item in cache.get("failures", [])
    )


def test_five_239_bottom_sites_are_configured_with_the_approved_cache_exception():
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    expected = {
        "高视阔步": {
            "url": "https://18118.73829.com/read.php?tid=480",
            "anchor": "作者\n高视阔步",
        },
        "重于泰山": {
            "url": "https://z0gcdiww42.669332.shop/bbs/topic.php?id=2877",
            "anchor": "作者重于泰山",
        },
        "独家记忆": {
            "url": "https://88888020827.897657.xyz/bbs/topic.php?id=20220",
            "anchor": "独家记忆",
            "stop_anchor": "下一贴:",
        },
        "致富龍哥": {
            "url": "https://88888020827.833567.xyz/bbs/topic.php?id=22613",
            "anchor": "致富龍哥",
            "stop_anchor": "下一贴:",
        },
        "岁月静好": {
            "url": "https://88888020827.833567.xyz/bbs/topic.php?id=20519",
            "anchor": "岁月静好",
            "stop_anchor": "下一贴:",
        },
    }
    bypassed = [
        "六合绝杀十码 235期",
        "六合稳杀十码 235期",
        "六合稳杀七码 235期",
        "花仙子 235期",
        "港澳爆庄 239期",
        "好料见证 239期",
    ]

    for name, site_expected in expected.items():
        matches = [item for item in targets if item.get("name") == name]
        assert len(matches) == 1
        target = matches[0]
        assert target["url"] == site_expected["url"]
        assert target["keywords"] == ["绝杀十码", "绝杀10码"]
        assert target["count"] == 10
        assert target["region"] == "bottom"
        assert target["anchor"] == site_expected["anchor"]
        assert target.get("stop_anchor") == site_expected.get("stop_anchor")
        assert target["issue_position_window"] == 10
        if name in {"重于泰山", "岁月静好", "独家记忆"}:
            assert target["disabled"] is True
            assert target["archive_tag"] == "用户指定封存"
        else:
            assert target.get("disabled") is not True

        exception = target["onboarding_exception"]
        assert exception["approved"] is True
        assert exception["type"] == "user_special_addition"
        assert exception["validated_issue"] == "239"
        assert exception["valid_history_issues"] == [
            str(issue) for issue in range(230, 240)
        ]
        assert exception["missing_history_issues"] == []
        assert exception["cache_failures_bypassed"] == bypassed


def test_five_scanned_bottom_sites_are_configured_with_the_approved_cache_exception():
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    expected = {
        "在所不惜": "https://6609960827.115646.xyz/bbs/topic.php?id=21389",
        "舒舒服服": "https://12388990827.794555.xyz/bbs/topic.php?id=21351",
        "云海尘清": "https://12388990827.794555.xyz/bbs/topic.php?id=21013",
        "人才辈出": "https://11008880827.876657.xyz/bbs/topic.php?id=20371",
        "东方红日": "https://0130190827.987860.xyz/bbs/topic.php?id=18511",
    }
    bypassed = [
        "六合绝杀十码 235期",
        "六合稳杀十码 235期",
        "六合稳杀七码 235期",
        "花仙子 235期",
    ]

    for name, url in expected.items():
        matches = [item for item in targets if item.get("name") == name]
        assert len(matches) == 1
        target = matches[0]
        assert target["url"] == url
        assert target["keywords"] == ["绝杀十码", "绝杀10码"]
        assert target["count"] == 10
        assert target["region"] == "bottom"
        assert target["anchor"] == name
        if name in {"舒舒服服", "云海尘清", "人才辈出"}:
            assert target["content_class"] == "content"
            assert target.get("stop_anchor") is None
        else:
            assert target["stop_anchor"] == "下一贴:"
        assert target["issue_position_window"] == 10
        if name == "舒舒服服":
            assert target["disabled"] is True
            assert target["archive_tag"] == "用户指定封存"
        else:
            assert target.get("disabled") is not True

        exception = target["onboarding_exception"]
        assert exception["approved"] is True
        assert exception["type"] == "user_special_addition"
        assert exception["validated_issue"] == "239"
        assert exception["valid_history_issues"] == [
            str(issue) for issue in range(230, 240)
        ]
        assert exception["missing_history_issues"] == []
        assert exception["cache_failures_bypassed"] == bypassed


@pytest.mark.parametrize(
    ("name", "url"),
    [
        ("彩界天花", "https://nlafoq9v.dh5565656.xyz/bbs/topic.php?id=1178"),
        ("独家记忆", "https://88888020827.897657.xyz/bbs/topic.php?id=20220"),
        ("舒舒服服", "https://12388990827.794555.xyz/bbs/topic.php?id=21351"),
    ],
)
def test_requested_254_anchor_failures_are_archived_and_removed_from_cache(name, url):
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    target = next(
        item for item in targets
        if item.get("name") == name and item.get("url") == url
    )
    assert target["disabled"] is True
    assert target["archive_tag"] == "用户指定封存"

    forbidden = json.loads(FORBIDDEN_PATH.read_text(encoding="utf-8"))["records"]
    assert any(
        item.get("name") == name
        and item.get("url") == url
        and item.get("status") == "archived"
        for item in forbidden
    )

    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    assert not any(
        item.get("name") == name and item.get("url") == url
        for item in cache.get("sites", [])
    )
    assert not any(
        item.get("name") == name and item.get("url") == url
        for item in cache.get("failures", [])
    )


def test_mayou_series_is_configured_as_a_bottom_seven_code_script_column():
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    url = "https://62139c.com/bbs/byg.js"
    matches = [item for item in targets if item.get("url") == url]

    assert len(matches) == 1
    target = matches[0]
    assert target["name"] == "码友系列"
    assert target["keywords"] == ["期杀"]
    assert target["count"] == 7
    assert target["region"] == "bottom"
    assert target["anchor"] == "码友心云"
    assert target["stop_anchor"] == "码友七郎"
    assert target["issue_position_window"] == 3
    assert target.get("disabled") is not True


def test_mayou_series_rows_are_scoped_inside_the_dedicated_script_section():
    content = (
        'document.writeln("<font color=\'#FF0000\'>【码友初心】</font><br>");'
        'document.writeln("240期必中平特【鼠+牛】√<br>");'
        'document.writeln("<font>【码友心云】</font><br>");'
        'document.writeln("255期杀【08.17.49.37.21.45.47】√<br>");'
        'document.writeln("256期杀【25.07.10.12.01.28.05】×<br>");'
        'document.writeln("257期杀【05.08.47.35.26.46.44】√<br>");'
        'document.writeln("<font>【码友七郎】</font><br>");'
        'document.writeln("257期杀【11.12.13.14.15.16.17】√<br>");'
    )
    target = {
        "url": "https://62139c.com/bbs/byg.js",
        "name": "码友系列",
        "keywords": ["期杀"],
        "count": 7,
        "region": "bottom",
        "anchor": "码友心云",
        "stop_anchor": "码友七郎",
        "issue_position_window": 3,
    }

    found = parse_generic(content, ["257", "256", "255", "254"], target)

    assert found["257"] == ["05", "08", "47", "35", "26", "46", "44"]
    assert found["256"] == ["25", "07", "10", "12", "01", "28", "05"]
    assert found["255"] == ["08", "17", "49", "37", "21", "45", "47"]
    assert "254" not in found
