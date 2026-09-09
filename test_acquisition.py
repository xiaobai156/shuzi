from urllib.error import HTTPError

import pytest

import crawler
from kill_numbers.acquisition import discovery, http_client
from kill_numbers.acquisition import documents as document_acquisition
from kill_numbers.acquisition.strategies import admin_article


def test_script_and_iframe_discovery_keeps_documents_separate():
    page_url = "https://example.test/topic/1"
    html = """
    <script src="/assets/app.js"></script>
    <script src="/assets/app.js"></script>
    <iframe src="/frame/content"></iframe>
    <iframe src="data:text/html,ignored"></iframe>
    """

    assert discovery.script_urls(html, page_url) == [
        "https://example.test/assets/app.js"
    ]
    assert discovery.iframe_urls(html, page_url) == [
        "https://example.test/frame/content"
    ]


def test_http_404_is_not_retried(monkeypatch):
    calls = []

    def fail_404(request, **_kwargs):
        calls.append(request.full_url)
        raise HTTPError(request.full_url, 404, "Not Found", None, None)

    monkeypatch.setattr(http_client, "validate_request_url", lambda _url: None)
    monkeypatch.setattr(http_client, "urlopen", fail_404)
    monkeypatch.setattr(http_client, "wait_for_host_slot", lambda _url: None)

    with pytest.raises(HTTPError) as error:
        http_client.fetch_bytes("https://example.test/missing")

    assert error.value.code == 404
    assert calls == ["https://example.test/missing"]


def test_landing_lookup_requires_exact_article_id():
    data = {
        "sectionData": {
            "one": {
                "adminArticles": [
                    {"id": "wanted-old", "title": "old"},
                    {"id": "wanted", "title": "current"},
                ]
            }
        }
    }

    assert admin_article.find_admin_article_in_landing_data(data, "wanted") == {
        "id": "wanted",
        "title": "current",
    }
    assert admin_article.find_admin_article_in_landing_data(data, "missing") is None


def test_admin_404_falls_back_to_exact_landing_article(monkeypatch):
    url = "https://example.test/article/admin/wanted?url=site"
    calls = []

    def fake_fetch_json(request_url: str):
        calls.append(request_url)
        if "/admin-articles/" in request_url:
            raise HTTPError(request_url, 404, "Not Found", None, None)
        return {
            "sectionData": {
                "one": {
                    "adminArticles": [
                        {
                            "id": "wanted",
                            "authorNickname": "作者",
                            "title": "211期",
                            "html": "211期 01 02 03",
                        }
                    ]
                }
            }
        }

    monkeypatch.setattr(admin_article, "fetch_json", fake_fetch_json)
    monkeypatch.setattr(
        admin_article,
        "fetch_text",
        lambda _url: pytest.fail("landing success must not fetch raw page"),
    )

    name, content = admin_article.crawl_admin_article_page(
        url,
        {"name": "目录"},
        ["211"],
        content_matches=lambda value, _target, _issues: "211期" in value,
        render_page=lambda _url: pytest.fail("landing success must not render"),
    )

    assert name == "作者"
    assert "211期 01 02 03" in content
    assert calls == [
        "https://example.test/api/proxy/admin-articles/wanted",
        "https://example.test/api/proxy/landing-page-data?url=site",
    ]


def test_static_discovery_does_not_merge_visible_and_script_documents(monkeypatch):
    page_url = "https://example.test/topic/1"
    raw_page = """
    <div>211期 专属栏目 01 02 03 04 05 06 07 08</div>
    <script>const old = "211期 专属栏目 09 10 11 12 13 14 15 16";</script>
    """
    monkeypatch.setattr(
        document_acquisition,
        "fetch_text",
        lambda url: raw_page if url == page_url else pytest.fail(url),
    )

    _name, source_documents = document_acquisition.discover_static_documents(page_url)
    page_document = next(item for item in source_documents if item.kind == "page")
    script_document = next(
        item for item in source_documents if item.kind == "inline_script"
    )

    assert "01 02 03 04 05 06 07 08" in page_document.content
    assert "09 10 11 12 13 14 15 16" not in page_document.content
    assert "09 10 11 12 13 14 15 16" in script_document.content
    assert all(
        not (
            "01 02 03 04 05 06 07 08" in item.content
            and "09 10 11 12 13 14 15 16" in item.content
        )
        for item in document_acquisition.parseable_documents(source_documents)
    )


def test_document_conflict_fails_closed_even_when_source_priorities_differ():
    current = document_acquisition.make_source_document(
        kind="page",
        url="https://example.test/topic/1",
        content="211期 专属栏目 01 02 03 04 05 06 07 08",
        priority=100,
    )
    old = document_acquisition.make_source_document(
        kind="decoded_script",
        url="https://example.test/old.js",
        content="211期 专属栏目 09 10 11 12 13 14 15 16",
        priority=75,
    )
    target = {
        "keywords": ["专属栏目"],
        "count": 8,
        "region": "top",
        "issue_position_window": 5,
    }

    with pytest.raises(ValueError, match="跨文档候选冲突"):
        crawler.parse_target_documents([current, old], target, ["211"])


def test_same_priority_document_conflict_fails_closed():
    first = document_acquisition.make_source_document(
        kind="page",
        url="https://example.test/current-a",
        content="211期 专属栏目 01 02 03 04 05 06 07 08",
        priority=100,
    )
    second = document_acquisition.make_source_document(
        kind="iframe",
        url="https://example.test/current-b",
        content="211期 专属栏目 09 10 11 12 13 14 15 16",
        priority=100,
    )
    target = {
        "keywords": ["专属栏目"],
        "count": 8,
        "region": "top",
        "issue_position_window": 5,
    }

    with pytest.raises(ValueError, match="跨文档候选冲突"):
        crawler.parse_target_documents([first, second], target, ["211"])


def test_numbers_are_never_joined_across_documents():
    first = document_acquisition.make_source_document(
        kind="page",
        url="https://example.test/a",
        content="211期 专属栏目 01 02 03 04",
        priority=100,
    )
    second = document_acquisition.make_source_document(
        kind="page",
        url="https://example.test/b",
        content="05 06 07 08",
        priority=100,
    )
    target = {
        "keywords": ["专属栏目"],
        "count": 8,
        "region": "top",
        "issue_position_window": 5,
    }

    issue_map, selected = crawler.parse_target_documents(
        [first, second],
        target,
        ["211"],
    )

    assert issue_map == {}
    assert selected is None
