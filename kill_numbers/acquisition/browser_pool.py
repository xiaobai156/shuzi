from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
import threading
from typing import Any

from kill_numbers.acquisition.http_client import HEADERS
from kill_numbers.acquisition.documents import make_source_document
from kill_numbers.acquisition.discovery import decode_strdecode_payloads
from kill_numbers.domain.models import SourceDocument
from kill_numbers.text_utils import remove_fragment


# 正文由 JS 异步填充的页面（如摇钱树第二）在 load 之后才写入数据，
# 固定等待会命中“加载中”占位符；用网络空闲作为就绪信号，并保留固定的稳定时间。
IDLE_WAIT_MS = 10000
SETTLE_AFTER_IDLE_MS = 1000


def _wait_for_network_idle(page: Any, timeout: int) -> None:
    """Best-effort readiness wait; pages that never go idle fall back to the fixed settle."""
    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout, IDLE_WAIT_MS))
    except Exception:
        pass


class _BrowserSession:
    """One browser/context owned and used by the dedicated browser thread."""

    def __init__(self, playwright_factory: Callable[[], Any] | None = None) -> None:
        if playwright_factory is None:
            from playwright.sync_api import sync_playwright

            playwright_factory = sync_playwright
        self._playwright = playwright_factory().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        self._context = self._browser.new_context(
            ignore_https_errors=True,
            user_agent=HEADERS["User-Agent"],
            locale="zh-CN",
        )

    def render(
        self,
        url: str,
        timeout: int,
        wait_until: str = "networkidle",
    ) -> tuple[str, str]:
        page = self._context.new_page()
        try:
            page.goto(url, wait_until=wait_until, timeout=timeout)
            _wait_for_network_idle(page, timeout)
            page.wait_for_timeout(SETTLE_AFTER_IDLE_MS)
            html_text = page.content()
            try:
                body_text = page.locator("body").inner_text(timeout=3000)
            except Exception:
                body_text = ""
            return body_text, html_text
        finally:
            try:
                page.close()
            except Exception:
                pass

    def close(self) -> None:
        try:
            self._context.close()
        finally:
            try:
                self._browser.close()
            finally:
                self._playwright.stop()


_POOL_LOCK = threading.Lock()
_POOL: ThreadPoolExecutor | None = None
_SESSION: _BrowserSession | None = None


def _browser_executor() -> ThreadPoolExecutor:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="crawler-browser")
        return _POOL


def _render_in_browser_thread(
    url: str,
    timeout: int,
    wait_until: str | None = None,
) -> tuple[str, str]:
    global _SESSION
    if _SESSION is None:
        _SESSION = _BrowserSession()
    try:
        if wait_until is None:
            return _SESSION.render(url, timeout)
        return _SESSION.render(url, timeout, wait_until)
    except Exception:
        failed_session = _SESSION
        _SESSION = None
        try:
            failed_session.close()
        except Exception:
            pass
        raise


def _close_in_browser_thread() -> None:
    global _SESSION
    if _SESSION is not None:
        session = _SESSION
        _SESSION = None
        session.close()


def close_browser_pool() -> None:
    """Close the lazily created shared browser; safe when no browser was used."""
    global _POOL
    with _POOL_LOCK:
        pool = _POOL
        _POOL = None
    if pool is None:
        return
    try:
        try:
            pool.submit(_close_in_browser_thread).result()
        except Exception:
            pass
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def _render_page_parts(
    url: str,
    timeout: int,
    wait_until: str | None = None,
) -> tuple[str, str]:
    page_url = remove_fragment(url)
    return _browser_executor().submit(
        _render_in_browser_thread, page_url, timeout, wait_until
    ).result()


def render_page_documents(
    url: str,
    timeout: int = 25000,
    wait_until: str | None = None,
) -> list[SourceDocument]:
    page_url = remove_fragment(url)
    if wait_until is None:
        body_text, html_text = _render_page_parts(url, timeout)
    else:
        body_text, html_text = _render_page_parts(url, timeout, wait_until)
    documents = []
    if body_text:
        documents.append(
            make_source_document(
                kind="browser_body",
                url=page_url,
                content=body_text,
                priority=60,
                metadata={"parseable": True},
            )
        )
    if html_text:
        documents.append(
            make_source_document(
                kind="browser_dom",
                url=page_url,
                content=html_text,
                priority=50,
                metadata={"parseable": True},
            )
        )
        for index, value in enumerate(decode_strdecode_payloads(html_text)):
            documents.append(
                make_source_document(
                    kind="browser_decoded",
                    url=f"{page_url}#browser-decoded-{index + 1}",
                    parent_url=page_url,
                    content=value,
                    priority=45,
                    metadata={
                        "parseable": True,
                        "authorization": "browser_decoded",
                    },
                )
            )
    return documents
