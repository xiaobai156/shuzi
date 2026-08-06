import crawler
import pytest
from kill_numbers.acquisition.documents import make_source_document


DETAIL_TARGET = {
    "name": "告别那时",
    "keywords": ["绝杀10码"],
    "count": 10,
    "region": "top",
    "anchor": "告别那时",
    "article_identity": "告别那时",
    "issue_position_window": 5,
    "special_parser": "identity_article_top_10",
}


LINK_TARGET = {
    "url": "https://a.ttss.vip/list.aspx?id=79&page=1",
    "name": "告别那时",
    "link_keywords": ["告别那时", "【绝杀10码】"],
    "keywords": ["绝杀10码"],
    "count": 10,
    "region": "top",
    "anchor": "告别那时",
    "article_identity": "告别那时",
    "issue_position_window": 5,
    "pagination_limit": 3,
    "pagination_next_text": "下一页",
    "special_parser": "ttss_paginated_identity_top_10",
}


def detail_fixture(identity: str = "告别那时") -> str:
    return (
        f"<h1>218期: {identity}【绝杀10码】已免费公开</h1>"
        "<p>提高速度,减少浏览流量，不保留大量往期记录!</p>"
        f"<p>218期 {identity} : 【39,41,02,43,42,46,14,23,31,11】 开 ?? 准</p>"
        f"<p>216期 {identity} : 【38,17,18,29,49,30,35,10,25,12】 开 37 准</p>"
        f"<p>213期 {identity} : 【40,20,21,38,46,34,23,07,04,28】 开 35 准</p>"
        f"<p>212期 {identity} : 【11,01,15,48,07,40,33,08,42,29】 开 06 准</p>"
    )


def source(url: str, content: str) -> object:
    return make_source_document(
        kind="page",
        url=url,
        content=content,
        priority=100,
        metadata={"parseable": True},
    )


def test_identity_article_top_10_parser_keeps_top_order_and_window():
    found = crawler.parse_target_content(
        detail_fixture(),
        DETAIL_TARGET,
        ["218", "216"],
    )

    assert found == {
        "218": ["39", "41", "02", "43", "42", "46", "14", "23", "31", "11"],
        "216": ["38", "17", "18", "29", "49", "30", "35", "10", "25", "12"],
    }


def test_identity_article_top_10_window_counts_only_valid_rows():
    target = {**DETAIL_TARGET, "issue_position_window": 1}

    assert crawler.parse_target_content(detail_fixture(), target, ["218"]) == {
        "218": ["39", "41", "02", "43", "42", "46", "14", "23", "31", "11"]
    }


def test_identity_article_top_10_parser_rejects_wrong_identity_and_conflicts():
    with pytest.raises(ValueError, match="身份不匹配"):
        crawler.parse_target_content(
            detail_fixture("其他站点"),
            DETAIL_TARGET,
            ["218"],
        )

    conflict = (
        "<h1>218期: 告别那时【绝杀10码】已免费公开</h1>"
        "<p>218期 告别那时 : 【39,41,02,43,42,46,14,23,31,11】 开 ?? 准</p>"
        "<p>218期 告别那时 : 【01,02,03,04,05,06,07,08,09,10】 开 ?? 准</p>"
    )
    with pytest.raises(ValueError, match="候选不唯一"):
        crawler.parse_target_content(conflict, DETAIL_TARGET, ["218"])


def test_paginated_chain_scans_all_pages_and_keeps_detail_evidence(monkeypatch):
    list_one = "<a href='list.aspx?id=79&page=2'>下一页</a>"
    list_two = (
        "<a href='article.aspx?id=972421'>"
        "218期: 告别那时【绝杀10码】已免费公开"
        "</a><a href='list.aspx?id=79&page=3'>下一页</a>"
    )
    list_three = "<p>没有更多资料</p>"
    pages = {
        LINK_TARGET["url"]: [source(LINK_TARGET["url"], list_one)],
        "https://a.ttss.vip/list.aspx?id=79&page=2": [
            source("https://a.ttss.vip/list.aspx?id=79&page=2", list_two)
        ],
        "https://a.ttss.vip/list.aspx?id=79&page=3": [
            source("https://a.ttss.vip/list.aspx?id=79&page=3", list_three)
        ],
        "https://a.ttss.vip/article.aspx?id=972421": [
            source(
                "https://a.ttss.vip/article.aspx?id=972421",
                detail_fixture(),
            )
        ],
    }
    seen = []

    def fake_discover(url: str):
        seen.append(url)
        return "中彩堂", pages[url]

    monkeypatch.setattr(crawler, "discover_static_documents", fake_discover)

    results, failure = crawler.crawl_one(LINK_TARGET, ["218"])

    assert failure is None
    assert len(results) == 1
    assert results[0].numbers == [
        "39", "41", "02", "43", "42", "46", "14", "23", "31", "11"
    ]
    assert results[0].evidence is not None
    assert results[0].evidence.source_url == "https://a.ttss.vip/article.aspx?id=972421"
    assert seen == [
        LINK_TARGET["url"],
        "https://a.ttss.vip/list.aspx?id=79&page=2",
        "https://a.ttss.vip/list.aspx?id=79&page=3",
        "https://a.ttss.vip/article.aspx?id=972421",
    ]


def test_paginated_chain_rejects_distinct_duplicate_article_links(monkeypatch):
    pages = {
        LINK_TARGET["url"]: [
            source(
                LINK_TARGET["url"],
                "<a href='list.aspx?id=79&page=2'>下一页</a>",
            )
        ],
        "https://a.ttss.vip/list.aspx?id=79&page=2": [
            source(
                "https://a.ttss.vip/list.aspx?id=79&page=2",
                "<a href='article.aspx?id=972421'>"
                "218期: 告别那时【绝杀10码】已免费公开</a>"
                "<a href='list.aspx?id=79&page=3'>下一页</a>",
            )
        ],
        "https://a.ttss.vip/list.aspx?id=79&page=3": [
            source(
                "https://a.ttss.vip/list.aspx?id=79&page=3",
                "<a href='article.aspx?id=972420'>"
                "218期: 告别那时【绝杀10码】已免费公开</a>",
            )
        ],
    }

    monkeypatch.setattr(crawler, "discover_static_documents", lambda url: ("中彩堂", pages[url]))

    _results, failure = crawler.crawl_one(LINK_TARGET, ["218"])

    assert failure is not None
    assert "专属链接候选冲突" in failure.reason


def test_paginated_chain_treats_disabled_next_link_as_terminal(monkeypatch):
    listing_url = LINK_TARGET["url"]
    pages = {
        listing_url: [
            source(
                listing_url,
                "<a href='article.aspx?id=972421'>"
                "218期: 告别那时【绝杀10码】已免费公开</a>"
                "<a href='#'>下一页</a>",
            )
        ],
        "https://a.ttss.vip/article.aspx?id=972421": [
            source(
                "https://a.ttss.vip/article.aspx?id=972421",
                detail_fixture(),
            )
        ],
    }
    seen = []

    def fake_discover(url: str):
        seen.append(url)
        return "中彩堂", pages[url]

    monkeypatch.setattr(crawler, "discover_static_documents", fake_discover)

    results, failure = crawler.crawl_one(LINK_TARGET, ["218"])

    assert failure is None
    assert len(results) == 1
    assert seen == [listing_url, "https://a.ttss.vip/article.aspx?id=972421"]


def test_formal_ttss_targets_are_configured_with_approved_history_exception():
    targets = {
        target["name"]: target
        for target in crawler.load_targets()
        if target.get("name") in {"告别那时", "私房一肖"}
    }

    assert set(targets) == {"告别那时", "私房一肖"}
    for name, target in targets.items():
        assert target["url"] == LINK_TARGET["url"]
        assert target["special_parser"] == "ttss_paginated_identity_top_10"
        assert target["region"] == "top"
        assert target["count"] == 10
        assert target["pagination_limit"] == 9
        exception = target["onboarding_exception"]
        assert exception["type"] == "insufficient_history"
        assert exception["approved"] is True
        assert exception["validated_issue"] == "218"
        assert exception["valid_history_issues"]
        assert exception["scope"] == "用户明确允许两站不足10期加入；仅放宽历史数量"
