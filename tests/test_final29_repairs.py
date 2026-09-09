import json
from pathlib import Path

import crawler
from kill_numbers.acquisition.documents import make_source_document


def source(url: str, content: str):
    return make_source_document(
        kind="page",
        url=url,
        content=content,
        priority=100,
        metadata={"parseable": True},
    )


def test_bukehuoque_uses_current_article_history_without_fake_rollover():
    target = next(t for t in crawler.load_targets() if t["name"] == "不可或缺")
    assert target["special_parser"] == "top_article_history"
    html = (
        "<h1>252期:频果报论坛【准杀八码】长期免费</h1>"
        "不可或缺 发表于"
        "<p>248期：【准杀八码】47.23.33.36.44.08.34.24开猪20准</p>"
        "<p>249期：【准杀八码】24.22.09.34.43.10.14.40开猴23准</p>"
        "<p>250期：【准杀八码】29.20.08.17.32.19.43.27开蛇14准</p>"
        "<p>251期：【准杀八码】15.29.39.21.42.32.33.05开牛30准</p>"
        "<p>252期：【准杀八码】34.42.21.41.17.10.48.36开0000准</p>"
        "上一篇:"
    )
    assert crawler.parse_target_content(html, target, ["252"]) == {
        "252": ["34", "42", "21", "41", "17", "10", "48", "36"]
    }


def test_hengcaifu_is_scoped_to_unique_jssm_script():
    target = next(t for t in crawler.load_targets() if t["name"] == "横财富")
    assert target["source_url_pattern"] == "/jssm\\.aspx"
    assert target["allowed_source_types"] == ["external_script"]
    assert target["anchor"] == "横财富"
    assert target["source_anchor"] == ""
    script = (
        'document.writeln("249期绝杀十码:11.26.12.38.02.41.22.24.15.45开:猴23准");'
        'document.writeln("250期绝杀十码:19.42.06.14.09.27.18.37.46.23开:蛇14准");'
        'document.writeln("251期绝杀十码:11.22.32.42.20.34.16.06.25.38开:牛30准");'
        'document.writeln("252期绝杀十码:12.29.17.34.13.36.02.07.06.44开:鸡22准");'
        'document.writeln("253期绝杀十码:22.35.48.36.18.13.49.21.11.05开:？00准");'
    )
    document = make_source_document(
        kind="external_script",
        url="https://a.995546.com/jssm.aspx",
        parent_url=target["url"],
        content=script,
        priority=70,
        metadata={"parseable": True},
    )
    result = crawler.parse_target_document_results([document], target, ["252"])
    assert result.issue_map == {
        "252": ["12", "29", "17", "34", "13", "36", "02", "07", "06", "44"]
    }


def test_majing_is_scoped_to_unique_amlxfs_script():
    target = next(t for t in crawler.load_targets() if t["name"] == "马经论坛")
    assert target["source_url_pattern"] == "/amlxfs\\.aspx"
    assert target["allowed_source_types"] == ["external_script"]
    assert not target.get("special_parser")
    script = (
        'document.writeln("<div>澳门马经论坛【绝杀十码】</div>");'
        'document.writeln("250期绝杀十码:25.47.20.35.37.28.11.21.19.14开:蛇14错");'
        'document.writeln("251期绝杀十码:02.33.22.38.25.41.24.01.08.06开:牛30准");'
        'document.writeln("252期绝杀十码:33.17.43.15.04.46.42.24.22.34开:？00准");'
    )
    assert crawler.parse_target_content(script, target, ["252"]) == {
        "252": ["33", "17", "43", "15", "04", "46", "42", "24", "22", "34"]
    }


def test_ttss_requested_issue_at_page_limit_does_not_fail_on_more_pages(monkeypatch):
    target = {
        "url": "https://a.test/list?page=1",
        "name": "私房一肖",
        "link_keywords": ["私房一肖", "【绝杀10码】"],
        "keywords": ["绝杀10码"],
        "count": 10,
        "region": "top",
        "anchor": "私房一肖",
        "article_identity": "私房一肖",
        "issue_position_window": 5,
        "pagination_limit": 3,
        "pagination_next_text": "下一页",
        "special_parser": "ttss_paginated_identity_top_10",
    }
    pages = {
        "https://a.test/list?page=1": [source("https://a.test/list?page=1", "<a href='list?page=2'>下一页</a>")],
        "https://a.test/list?page=2": [source("https://a.test/list?page=2", "<a href='list?page=3'>下一页</a>")],
        "https://a.test/list?page=3": [source(
            "https://a.test/list?page=3",
            "<a href='article?id=252'>252期: 私房一肖【绝杀10码】已免费公开</a>"
            "<a href='list?page=4'>下一页</a>",
        )],
        "https://a.test/article?id=252": [source(
            "https://a.test/article?id=252",
            "<h1>252期: 私房一肖【绝杀10码】已免费公开</h1>"
            "<p>252期 私房一肖 : 【01,02,03,04,05,06,07,08,09,10】 开 ?? 准</p>",
        )],
    }
    seen = []
    def fake_discover(url):
        seen.append(url)
        return "测试", pages[url]
    monkeypatch.setattr(crawler, "discover_static_documents", fake_discover)
    results, failure = crawler.crawl_one(target, ["252"])
    assert failure is None
    assert [r.numbers for r in results] == [["01","02","03","04","05","06","07","08","09","10"]]
    assert "https://a.test/list?page=4" not in seen


def test_ttss_page_limit_still_fails_when_requested_issue_not_found(monkeypatch):
    target = {
        "url": "https://a.test/list?page=1",
        "name": "私房一肖",
        "link_keywords": ["私房一肖", "【绝杀10码】"],
        "keywords": ["绝杀10码"],
        "count": 10,
        "region": "top",
        "anchor": "私房一肖",
        "article_identity": "私房一肖",
        "issue_position_window": 5,
        "pagination_limit": 2,
        "pagination_next_text": "下一页",
        "special_parser": "ttss_paginated_identity_top_10",
    }
    pages = {
        "https://a.test/list?page=1": [source("https://a.test/list?page=1", "<a href='list?page=2'>下一页</a>")],
        "https://a.test/list?page=2": [source("https://a.test/list?page=2", "<a href='list?page=3'>下一页</a>")],
    }
    monkeypatch.setattr(crawler, "discover_static_documents", lambda url: ("测试", pages[url]))
    _results, failure = crawler.crawl_one(target, ["252"])
    assert failure is not None
    assert "分页上限 2 内没有找到当前身份文章" in failure.reason


def test_empty_source_anchor_uses_unique_url_scoped_document_identity():
    target = {
        "url": "https://example.test/",
        "name": "横财富",
        "keywords": ["绝杀十码"],
        "count": 10,
        "region": "bottom",
        "anchor": "横财富",
        "source_url_pattern": r"/jssm\.aspx",
        "source_anchor": "",
        "allowed_source_types": ["external_script"],
    }
    script = make_source_document(
        kind="external_script",
        url="https://example.test/jssm.aspx",
        parent_url=target["url"],
        content=(
            'document.writeln("251期绝杀十码:11.22.32.42.20.34.16.06.25.38开:牛30准");'
            'document.writeln("252期绝杀十码:12.29.17.34.13.36.02.07.06.44开:鸡22准");'
        ),
        priority=70,
        metadata={"parseable": True},
    )
    result = crawler.parse_target_document_results([script], target, ["252"])
    assert result.issue_map == {
        "252": ["12", "29", "17", "34", "13", "36", "02", "07", "06", "44"]
    }
    assert result.source_documents["252"].url.endswith("/jssm.aspx")


def test_empty_source_anchor_rejects_multiple_url_scoped_documents():
    target = {
        "url": "https://example.test/",
        "name": "横财富",
        "keywords": ["绝杀十码"],
        "count": 10,
        "region": "bottom",
        "anchor": "横财富",
        "source_url_pattern": r"/jssm\.aspx",
        "source_anchor": "",
        "allowed_source_types": ["external_script"],
    }
    documents = [
        make_source_document(
            kind="external_script",
            url=f"https://example.test/{prefix}/jssm.aspx",
            parent_url=target["url"],
            content='document.writeln("252期绝杀十码:12.29.17.34.13.36.02.07.06.44开:鸡22准");',
            priority=70,
            metadata={"parseable": True},
        )
        for prefix in ("a", "b")
    ]
    try:
        crawler.parse_target_document_results(documents, target, ["252"])
    except Exception as exc:
        assert "空 source_anchor 仅允许唯一专属来源文档" in str(exc)
    else:
        raise AssertionError("multiple URL-scoped documents must fail closed")


def test_unique_url_identity_survives_formal_evidence_revalidation(monkeypatch):
    target = {
        "url": "https://example.test/",
        "name": "横财富",
        "keywords": ["绝杀十码"],
        "count": 10,
        "region": "bottom",
        "anchor": "横财富",
        "source_url_pattern": r"/jssm\.aspx",
        "source_anchor": "",
        "allowed_source_types": ["external_script"],
    }
    document = make_source_document(
        kind="external_script",
        url="https://example.test/jssm.aspx",
        parent_url=target["url"],
        content=(
            'document.writeln("251期绝杀十码:11.22.32.42.20.34.16.06.25.38开:牛30准");'
            'document.writeln("252期绝杀十码:12.29.17.34.13.36.02.07.06.44开:鸡22准");'
        ),
        priority=70,
        metadata={"parseable": True},
    )
    monkeypatch.setattr(
        crawler,
        "fetch_target_documents",
        lambda _target, _issues=None: ("横财富", [document]),
    )
    results, failure = crawler.crawl_one(target, ["252"])
    assert failure is None
    assert len(results) == 1
    assert results[0].numbers == ["12", "29", "17", "34", "13", "36", "02", "07", "06", "44"]
    assert results[0].evidence.scope_kind == "source_url_identity"
    assert results[0].evidence.source_identity.endswith("/jssm.aspx")
    assert results[0].evidence.anchor == ""
