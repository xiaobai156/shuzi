import pytest

import crawler
import check_duplicates
from kill_numbers.acquisition.documents import make_source_document
from kill_numbers.application import crawl_service
from kill_numbers.application.crawl_service import CrawlDependencies


def source_document(kind, url, content, priority=100):
    return make_source_document(
        kind=kind,
        url=url,
        content=content,
        priority=priority,
        metadata={"parseable": True},
    )


def unexpected_link_chain(_target, _issues):
    raise AssertionError("本测试不应调用链接链解析器")


def test_fetch_layer_does_not_launch_browser_before_http_content_is_parsed(monkeypatch):
    url = "https://example.test/topic/235.html"
    http_document = source_document(
        "page",
        url,
        "235期 杀10码 [01 02 03 04 05 06 07 08 09 10]",
    )
    browser_calls = []

    monkeypatch.setattr(
        crawler,
        "discover_static_documents",
        lambda requested_url, encoding=None: ("测试站", [http_document]),
    )
    monkeypatch.setattr(
        crawler,
        "render_page_documents",
        lambda requested_url: browser_calls.append(requested_url) or [],
    )

    name, documents = crawler.fetch_target_documents(
        {
            "name": "测试站",
            "url": url,
            "browser_fallback": True,
        },
        ["235"],
    )

    assert name == "测试站"
    assert documents == [http_document]
    assert browser_calls == []


def test_formal_pipeline_uses_browser_once_when_http_lacks_requested_issue(monkeypatch):
    url = "https://example.test/topic/235.html"
    numbers = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10"]
    http_document = source_document("page", url, "页面正在加载")
    browser_document = source_document(
        "browser_dom",
        url,
        "测试站\n235期 杀10码 [01 02 03 04 05 06 07 08 09 10]",
        priority=120,
    )
    browser_calls = []

    def parse_documents(documents, _target, _issues):
        for document in documents:
            if document.kind == "browser_dom":
                return {"235": numbers}, document
        return {}, None

    monkeypatch.setattr(
        crawl_service,
        "render_page_documents",
        lambda requested_url: browser_calls.append(requested_url) or [browser_document],
    )
    dependencies = CrawlDependencies(
        fetch_target_documents=lambda _target, _issues: ("测试站", [http_document]),
        parse_target_documents=parse_documents,
        crawl_zuojianzifu_link_chain=unexpected_link_chain,
        contract_is_split_across_documents=lambda _documents, _target: False,
        save_debug_page=lambda *_args: None,
    )

    results, failure = crawl_service.run_formal_crawl_target(
        dependencies,
        {
            "name": "测试站",
            "url": url,
            "browser_fallback": True,
            "count": 10,
            "region": "top",
            "anchor": "测试站",
        },
        ["235"],
    )

    assert failure is None
    assert browser_calls == [url]
    assert [(result.issue, result.numbers) for result in results] == [("235", numbers)]
    evidence = results[0].evidence
    assert evidence is not None
    assert evidence.source_kind == "browser_dom"


def duplicate_history_target(url):
    return {
        "name": "测试站",
        "url": url,
        "browser_fallback": True,
        "keywords": ["杀10码"],
        "count": 10,
        "region": "top",
        "anchor": "测试站",
        "issue_position_window": 10,
    }


@pytest.mark.parametrize("name", ["六合稳杀十码", "马经论坛", "摇钱树第二"])
def test_verified_dynamic_dom_sites_use_fixed_browser(name):
    target = next(
        target for target in crawler.load_targets() if target["name"] == name
    )

    assert target["browser"] is True


def test_swing_money_tree_waits_for_load_not_domcontentloaded():
    target = next(
        target for target in crawler.load_targets() if target["name"] == "摇钱树第二"
    )

    # 该站数据由 JS 异步填充：domcontentloaded 会在填充前返回，实测 3/4 命中占位符；
    # load 实测 4/4 就绪，故固定为 load。
    assert target["browser_wait_until"] == "load"


def test_http_complete_script_site_keeps_browser_as_fallback():
    target = next(
        target for target in crawler.load_targets() if target["name"] == "师太"
    )

    assert target["browser_fallback"] is True
    assert target.get("browser") is not True


def test_fixed_browser_target_skips_static_http_discovery(monkeypatch):
    target = {
        "url": "https://example.test/dynamic",
        "name": "动态站",
        "region": "top",
        "count": 3,
        "browser": True,
    }
    rendered = source_document(
        "browser_body",
        target["url"],
        "236期 01 02 03",
        priority=60,
    )
    monkeypatch.setattr(crawler, "render_page_documents", lambda _url: [rendered])
    monkeypatch.setattr(
        crawler,
        "discover_static_documents",
        lambda *_args, **_kwargs: pytest.fail("固定浏览器站不得先抓静态页面"),
    )

    name, documents = crawler.fetch_target_documents(target, ["236"])

    assert name == "动态站"
    assert documents == [rendered]


def ten_issue_history():
    rows = [
        f"{issue}期 杀10码 [01 02 03 04 05 06 07 08 09 10]"
        for issue in range(236, 226, -1)
    ]
    return "测试站\n" + "\n".join(rows)


def five_issue_history():
    rows = [
        f"{issue}期 杀10码 [01 02 03 04 05 06 07 08 09 10]"
        for issue in range(236, 231, -1)
    ]
    return "测试站\n" + "\n".join(rows)


def test_duplicate_history_discovery_keeps_http_when_window_is_complete(monkeypatch):
    url = "https://example.test/topic/history.html"
    http_document = source_document("page", url, ten_issue_history())
    browser_calls = []

    monkeypatch.setattr(
        crawler,
        "fetch_target_documents",
        lambda _target, _issues: ("测试站", [http_document]),
    )
    monkeypatch.setattr(
        crawler,
        "render_page_documents",
        lambda requested_url: browser_calls.append(requested_url) or [],
    )

    name, documents = check_duplicates.fetch_target_content(
        duplicate_history_target(url)
    )

    assert name == "测试站"
    assert documents == [http_document]
    assert browser_calls == []


def test_duplicate_history_discovery_renders_when_http_window_is_incomplete(monkeypatch):
    url = "https://example.test/topic/history.html"
    http_document = source_document("page", url, "页面正在加载")
    browser_document = source_document("browser_dom", url, ten_issue_history(), priority=120)
    browser_calls = []

    monkeypatch.setattr(
        crawler,
        "fetch_target_documents",
        lambda _target, _issues: ("测试站", [http_document]),
    )
    monkeypatch.setattr(
        crawler,
        "render_page_documents",
        lambda requested_url: browser_calls.append(requested_url) or [browser_document],
    )

    name, documents = check_duplicates.fetch_target_content(
        duplicate_history_target(url)
    )

    assert name == "测试站"
    assert documents == [http_document, browser_document]
    assert browser_calls == [url]


def test_duplicate_snapshot_uses_complete_browser_history_over_partial_http(monkeypatch):
    url = "https://example.test/topic/history.html"
    http_document = source_document("page", url, five_issue_history())
    browser_document = source_document("browser_dom", url, ten_issue_history(), priority=60)

    monkeypatch.setattr(
        crawler,
        "fetch_target_documents",
        lambda _target, _issues: ("测试站", [http_document]),
    )
    monkeypatch.setattr(
        crawler,
        "render_page_documents",
        lambda _url: [browser_document],
    )

    snapshot = check_duplicates.snapshot_for_target(duplicate_history_target(url))

    assert snapshot.available_issues == [
        "236", "235", "234", "233", "232", "231", "230", "229", "228", "227"
    ]
    assert snapshot.selected_content == browser_document.content


def test_duplicate_history_rejects_shifted_http_and_browser_windows(monkeypatch):
    url = "https://example.test/topic/history.html"
    http_content = "测试站\n" + "\n".join(
        f"{issue}期 杀10码 [01 02 03 04 05 06 07 08 09 10]"
        for issue in range(235, 230, -1)
    )
    browser_content = "测试站\n" + "\n".join(
        f"{issue}期 杀10码 [01 02 03 04 05 06 07 08 09 10]"
        for issue in range(236, 231, -1)
    )
    monkeypatch.setattr(
        crawler,
        "fetch_target_documents",
        lambda _target, _issues: ("测试站", [source_document("page", url, http_content)]),
    )
    monkeypatch.setattr(
        crawler,
        "render_page_documents",
        lambda _url: [source_document("browser_dom", url, browser_content, priority=60)],
    )

    with pytest.raises(ValueError, match="HTTP 与浏览器识别到的历史期数"):
        check_duplicates.snapshot_for_target(duplicate_history_target(url))


def test_duplicate_history_reports_browser_failure(monkeypatch):
    url = "https://example.test/topic/history.html"
    http_document = source_document("page", url, "页面正在加载")

    monkeypatch.setattr(
        crawler,
        "fetch_target_documents",
        lambda _target, _issues: ("测试站", [http_document]),
    )
    monkeypatch.setattr(
        crawler,
        "render_page_documents",
        lambda _url: (_ for _ in ()).throw(RuntimeError("browser crashed")),
    )

    with pytest.raises(RuntimeError, match="浏览器抓取失败.*browser crashed"):
        check_duplicates.snapshot_for_target(duplicate_history_target(url))


def test_formal_pipeline_reports_browser_failure(monkeypatch):
    url = "https://example.test/topic/235.html"
    http_document = source_document("page", url, "页面正在加载")
    dependencies = CrawlDependencies(
        fetch_target_documents=lambda _target, _issues: ("测试站", [http_document]),
        parse_target_documents=lambda _documents, _target, _issues: ({}, None),
        crawl_zuojianzifu_link_chain=unexpected_link_chain,
        contract_is_split_across_documents=lambda _documents, _target: False,
        save_debug_page=lambda *_args: None,
    )
    monkeypatch.setattr(
        crawl_service,
        "render_page_documents",
        lambda _url: (_ for _ in ()).throw(RuntimeError("browser crashed")),
    )

    results, failure = crawl_service.run_formal_crawl_target(
        dependencies,
        {
            "name": "测试站",
            "url": url,
            "browser_fallback": True,
            "count": 10,
            "region": "top",
            "anchor": "测试站",
        },
        ["235"],
    )

    assert results == []
    assert failure is not None
    assert failure.reason == "浏览器抓取失败：browser crashed"
