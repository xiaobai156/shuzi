import gzip
from email.message import Message
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

import crawler
from kill_numbers.acquisition import discovery, http_client
from kill_numbers.acquisition import browser_pool
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


@pytest.mark.parametrize("kind", ["admin", "manager", "lottery"])
def test_dynamic_article_id_is_parsed_for_every_supported_route(kind):
    assert (
        admin_article.parse_admin_article_id(
            f"https://example.test/article/{kind}/record-123?url=site"
        )
        == "record-123"
    )


def test_browser_session_reuses_browser_and_context_for_multiple_pages():
    calls = []

    class FakeLocator:
        def inner_text(self, timeout):
            calls.append(("inner_text", timeout))
            return "正文"

    class FakePage:
        def goto(self, url, wait_until, timeout):
            calls.append(("goto", url, wait_until, timeout))

        def wait_for_load_state(self, state, timeout):
            calls.append(("wait_for_load_state", state, timeout))

        def wait_for_timeout(self, timeout):
            calls.append(("wait", timeout))

        def content(self):
            return "<body>正文</body>"

        def locator(self, selector):
            assert selector == "body"
            return FakeLocator()

        def close(self):
            calls.append(("page_close",))

    class FakeContext:
        def new_page(self):
            calls.append(("new_page",))
            return FakePage()

        def close(self):
            calls.append(("context_close",))

    class FakeBrowser:
        def new_context(self, **kwargs):
            calls.append(("new_context", kwargs))
            return FakeContext()

        def close(self):
            calls.append(("browser_close",))

    class FakeChromium:
        def launch(self, **kwargs):
            calls.append(("launch", kwargs))
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

        def stop(self):
            calls.append(("playwright_stop",))

    class FakeStarter:
        def start(self):
            calls.append(("playwright_start",))
            return FakePlaywright()

    session = browser_pool._BrowserSession(lambda: FakeStarter())
    try:
        assert session.render("https://example.test/one", 1000) == (
            "正文",
            "<body>正文</body>",
        )
        assert session.render("https://example.test/two", 2000) == (
            "正文",
            "<body>正文</body>",
        )
        assert session.render(
            "https://example.test/dynamic", 3000, "domcontentloaded"
        ) == ("正文", "<body>正文</body>")
    finally:
        session.close()

    assert sum(call[0] == "playwright_start" for call in calls) == 1
    assert sum(call[0] == "launch" for call in calls) == 1
    assert sum(call[0] == "new_context" for call in calls) == 1
    assert sum(call[0] == "new_page" for call in calls) == 3
    assert sum(call[0] == "page_close" for call in calls) == 3
    assert ("goto", "https://example.test/dynamic", "domcontentloaded", 3000) in calls
    # load 之后先等网络空闲再取正文，固定等待读不到异步填充的内容。
    idle_waits = [call for call in calls if call[0] == "wait_for_load_state"]
    assert len(idle_waits) == 3
    assert all(
        state == "networkidle" and timeout <= browser_pool.IDLE_WAIT_MS
        for _, state, timeout in idle_waits
    )


def test_browser_session_reads_content_after_async_fill():
    """正文由 JS 异步填充的页面：网络空闲前读取只会拿到“加载中”占位符。"""

    class AsyncPage:
        filled = False

        def goto(self, *_args, **_kwargs):
            pass

        def wait_for_load_state(self, state, timeout):
            assert state == "networkidle"
            assert timeout <= browser_pool.IDLE_WAIT_MS
            self.filled = True

        def wait_for_timeout(self, _timeout):
            pass

        def content(self):
            if not self.filled:
                return "<body>加载中…</body>"
            return "<body>268期:绝杀10码</body>"

        def locator(self, _selector):
            page = self

            class Locator:
                def inner_text(self, timeout):
                    return "268期:绝杀10码" if page.filled else "加载中…"

            return Locator()

        def close(self):
            pass

    session = object.__new__(browser_pool._BrowserSession)
    session._context = type("Context", (), {"new_page": lambda self: AsyncPage()})()

    assert session.render("https://example.test/dynamic", 25000, "load") == (
        "268期:绝杀10码",
        "<body>268期:绝杀10码</body>",
    )


def test_browser_cleanup_error_does_not_mask_render_error(monkeypatch):
    class FailingSession:
        def render(self, _url, _timeout):
            raise RuntimeError("render failed")

        def close(self):
            raise ValueError("close failed")

    monkeypatch.setattr(browser_pool, "_SESSION", FailingSession())

    with pytest.raises(RuntimeError, match="render failed"):
        browser_pool._render_in_browser_thread("https://example.test", 1000)

    assert browser_pool._SESSION is None


def test_page_close_error_does_not_discard_successful_render():
    class FakeLocator:
        def inner_text(self, timeout):
            return "正文"

    class FakePage:
        def goto(self, *_args, **_kwargs):
            pass

        def wait_for_load_state(self, *_args, **_kwargs):
            pass

        def wait_for_timeout(self, _timeout):
            pass

        def content(self):
            return "<body>正文</body>"

        def locator(self, _selector):
            return FakeLocator()

        def close(self):
            raise RuntimeError("close failed")

    session = object.__new__(browser_pool._BrowserSession)
    session._context = type("Context", (), {"new_page": lambda self: FakePage()})()

    assert session.render("https://example.test", 1000) == (
        "正文",
        "<body>正文</body>",
    )


def test_browser_documents_keep_body_dom_and_decoded_sources_separate(monkeypatch):
    monkeypatch.setattr(
        browser_pool,
        "_render_page_parts",
        lambda _url, _timeout: ("可见正文", "<html><body>可见正文</body></html>"),
    )
    monkeypatch.setattr(
        browser_pool,
        "decode_strdecode_payloads",
        lambda _html: ["解码正文"],
    )

    documents = browser_pool.render_page_documents("https://example.test/page#part")

    assert [document.kind for document in documents] == [
        "browser_body",
        "browser_dom",
        "browser_decoded",
    ]
    assert documents[-1].metadata["authorization"] == "browser_decoded"
    assert documents[-1].parent_url == "https://example.test/page"


def test_shared_browser_executor_opens_lazily_and_closes_once(monkeypatch):
    calls = []

    class FakeSession:
        def __init__(self):
            calls.append("start")

        def render(self, url, timeout):
            calls.append((url, timeout))
            return "body", "html"

        def close(self):
            calls.append("close")

    monkeypatch.setattr(browser_pool, "_BrowserSession", FakeSession)
    monkeypatch.setattr(browser_pool, "_POOL", None)
    monkeypatch.setattr(browser_pool, "_SESSION", None)

    assert browser_pool._render_page_parts("https://example.test/page#part", 123) == (
        "body",
        "html",
    )
    browser_pool.close_browser_pool()

    assert calls == ["start", ("https://example.test/page", 123), "close"]
    assert browser_pool._POOL is None
    assert browser_pool._SESSION is None


def test_http_404_is_not_retried(monkeypatch):
    calls = []

    def fail_404(request, **_kwargs):
        calls.append(request.full_url)
        raise HTTPError(request.full_url, 404, "Not Found", Message(), None)

    monkeypatch.setattr(http_client, "urlopen", fail_404)
    monkeypatch.setattr(http_client, "wait_for_host_slot", lambda _url: None)

    with pytest.raises(HTTPError) as error:
        http_client.fetch_bytes("https://example.test/missing")

    assert error.value.code == 404
    assert calls == ["https://example.test/missing"]


def test_http_nontransient_error_is_not_retried(monkeypatch):
    calls = []

    def fail_403(request, **_kwargs):
        calls.append(request.full_url)
        raise HTTPError(request.full_url, 403, "Forbidden", Message(), None)

    monkeypatch.setattr(http_client, "urlopen", fail_403)
    monkeypatch.setattr(http_client, "wait_for_host_slot", lambda _url: None)

    with pytest.raises(HTTPError) as error:
        http_client.fetch_bytes("https://example.test/forbidden")

    assert error.value.code == 403
    assert calls == ["https://example.test/forbidden"]


def test_http_retry_count_is_bounded_to_two_attempts(monkeypatch):
    calls = []

    def fail(request, **_kwargs):
        calls.append(request.full_url)
        raise RuntimeError("permanent failure")

    monkeypatch.setattr(http_client, "urlopen", fail)
    monkeypatch.setattr(http_client, "wait_for_host_slot", lambda _url: None)
    monkeypatch.setattr(http_client.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="permanent failure"):
        http_client.fetch_bytes("https://example.test/fail")

    assert calls == ["https://example.test/fail", "https://example.test/fail"]


def test_transient_http_error_uses_one_curl_fallback_after_bounded_retries(monkeypatch):
    calls = []

    def fail_503(request, **_kwargs):
        calls.append(request.full_url)
        raise HTTPError(request.full_url, 503, "Unavailable", Message(), None)

    monkeypatch.setattr(http_client, "urlopen", fail_503)
    monkeypatch.setattr(http_client, "wait_for_host_slot", lambda _url: None)
    monkeypatch.setattr(http_client.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(http_client, "curl_fetch_bytes", lambda _url, timeout: b"ok")

    assert http_client.fetch_bytes("https://example.test/retry") == b"ok"
    assert calls == ["https://example.test/retry", "https://example.test/retry"]


def test_decode_response_handles_gzip_and_declared_legacy_charset():
    html = '<meta charset="gb18030">正文'.encode("gb18030")

    assert http_client.decode_response(gzip.compress(html)) == '<meta charset="gb18030">正文'


def test_curl_fetch_uses_single_native_request(monkeypatch):
    monkeypatch.setattr(http_client.shutil, "which", lambda _name: "curl.exe")
    captured = {}

    def fake_run(cmd, capture_output):
        captured["cmd"] = cmd
        captured["capture_output"] = capture_output
        return SimpleNamespace(returncode=0, stdout=b"payload", stderr=b"")

    monkeypatch.setattr(http_client.subprocess, "run", fake_run)

    assert http_client.curl_fetch_bytes("https://example.test/data", timeout=9) == b"payload"
    assert captured["cmd"].count("https://example.test/data") == 1
    assert "--retry" not in captured["cmd"]


def test_fetch_text_and_json_use_successful_http_response(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"value": 1}'

    monkeypatch.setattr(http_client, "urlopen", lambda *_args, **_kwargs: FakeResponse())
    monkeypatch.setattr(http_client, "wait_for_host_slot", lambda _url: None)

    assert http_client.fetch_text("https://example.test/data") == '{"value": 1}'
    assert http_client.fetch_json("https://example.test/data") == {"value": 1}


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
            raise HTTPError(request_url, 404, "Not Found", Message(), None)
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


def test_admin_non_404_api_failure_never_uses_landing_or_browser(monkeypatch):
    url = "https://example.test/article/admin/wanted?url=site"

    monkeypatch.setattr(
        admin_article,
        "fetch_json",
        lambda _url: (_ for _ in ()).throw(RuntimeError("TLS failed")),
    )

    with pytest.raises(RuntimeError, match="TLS failed"):
        admin_article.crawl_admin_article_page(
            url,
            {"name": "目录"},
            ["211"],
            content_matches=lambda *_args: False,
            render_page=lambda _url: pytest.fail("非404错误不得浏览器兜底"),
        )


def test_admin_complete_api_record_missing_requested_issue_does_not_fallback(monkeypatch):
    url = "https://example.test/article/admin/wanted?url=site"
    calls = []

    def fake_fetch_json(request_url):
        calls.append(request_url)
        return {
            "id": "wanted",
            "authorNickname": "作者",
            "title": "210期",
            "html": "210期 01 02 03",
        }

    monkeypatch.setattr(admin_article, "fetch_json", fake_fetch_json)

    name, content = admin_article.crawl_admin_article_page(
        url,
        {"name": "目录"},
        ["211"],
        content_matches=lambda value, _target, _issues: "211期" in value,
        render_page=lambda _url: pytest.fail("完整API记录不得浏览器兜底"),
    )

    assert name == "作者"
    assert "210期 01 02 03" in content
    assert calls == ["https://example.test/api/proxy/admin-articles/wanted"]


def test_admin_api_record_id_mismatch_fails_closed(monkeypatch):
    url = "https://example.test/article/admin/wanted?url=site"
    monkeypatch.setattr(
        admin_article,
        "fetch_json",
        lambda _url: {"id": "wrong", "html": "211期 01 02 03"},
    )

    with pytest.raises(ValueError, match="文章 ID 不一致"):
        admin_article.crawl_admin_article_page(
            url,
            {"name": "目录"},
            ["211"],
            content_matches=lambda *_args: True,
            render_page=lambda _url: pytest.fail("身份冲突不得浏览器兜底"),
        )


def test_identity_article_api_requires_matching_author_and_title():
    target = {
        "special_parser": "identity_article_bottom_10",
        "article_identity": "作者甲",
    }

    with pytest.raises(ValueError, match="作者身份不匹配"):
        admin_article.validate_admin_article_record(
            {"id": "wanted", "authorNickname": "作者乙", "title": "绝杀10码"},
            "wanted",
            target,
        )
    with pytest.raises(ValueError, match="接口标题不匹配"):
        admin_article.validate_admin_article_record(
            {"id": "wanted", "authorNickname": "作者甲", "title": "普通资料"},
            "wanted",
            target,
        )


def test_admin_404_can_render_only_after_landing_and_raw_page_are_empty(monkeypatch):
    url = "https://example.test/article/manager/wanted?url=site"
    calls = []

    def fake_fetch_json(request_url):
        calls.append(request_url)
        if "manager-articles" in request_url:
            raise HTTPError(request_url, 404, "Not Found", Message(), None)
        return {"sectionData": {}}

    monkeypatch.setattr(admin_article, "fetch_json", fake_fetch_json)
    monkeypatch.setattr(admin_article, "fetch_text", lambda _url: "页面空壳")

    name, content = admin_article.crawl_admin_article_page(
        url,
        {
            "api_url": "https://example.test/api/proxy/manager-articles/wanted",
            "name": "目录",
        },
        ["211"],
        content_matches=lambda value, _target, _issues: "211期" in value,
        render_page=lambda requested_url: calls.append(requested_url)
        or "作者甲\n211期 01 02 03",
    )

    assert name == "example.test"
    assert "211期" in content
    assert calls[-1] == url


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


def test_static_discovery_labels_external_script_and_iframe_sources(monkeypatch):
    page_url = "https://example.test/topic/1"
    responses = {
        page_url: (
            '<div>主页</div><script src="/data.js"></script>'
            '<iframe src="/frame.html"></iframe>'
        ),
        "https://example.test/data.js": "脚本数据",
        "https://example.test/frame.html": "<div>框架数据</div>",
    }
    monkeypatch.setattr(
        document_acquisition,
        "fetch_text",
        lambda url, **_kwargs: responses[url],
    )

    _name, documents = document_acquisition.discover_static_documents(page_url)
    by_kind = {document.kind: document for document in documents}

    assert by_kind["external_script"].metadata["authorization"] == "external_script"
    assert by_kind["iframe"].metadata["authorization"] == "iframe"
    assert by_kind["iframe"].parent_url == page_url
    assert by_kind["external_script"] not in document_acquisition.parseable_documents(documents)
    assert by_kind["external_script"] in document_acquisition.parseable_documents(
        documents, ["external_script"]
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
        "allowed_source_types": ["decoded_script"],
    }

    with pytest.raises(ValueError, match="跨文档候选冲突"):
        crawler.parse_target_documents([current, old], target, ["211"])


def test_decoded_script_is_ignored_without_explicit_target_authorization():
    decoded = document_acquisition.make_source_document(
        kind="decoded_script_stream",
        url="https://example.test/data.js#decoded-stream",
        content="栏目标题\n211期 专属栏目 01 02 03 04 05 06 07 08",
        priority=75,
        metadata={"parseable": True, "authorization": "decoded_script"},
    )
    target = {
        "keywords": ["专属栏目"],
        "count": 8,
        "region": "top",
        "anchor": "栏目标题",
    }

    assert crawler.parse_target_documents([decoded], target, ["211"]) == ({}, None)

    authorized = {**target, "allowed_source_types": ["decoded_script"]}
    issue_map, selected = crawler.parse_target_documents([decoded], authorized, ["211"])
    assert issue_map == {"211": ["01", "02", "03", "04", "05", "06", "07", "08"]}
    assert selected is decoded


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
