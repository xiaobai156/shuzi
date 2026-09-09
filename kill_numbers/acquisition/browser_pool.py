import os
import threading

from kill_numbers.acquisition.http_client import HEADERS
from kill_numbers.acquisition.documents import make_source_document
from kill_numbers.acquisition.discovery import decode_strdecode_payloads
from kill_numbers.domain.models import SourceDocument
from kill_numbers.text_utils import remove_fragment


_BROWSER_CONCURRENCY = max(1, int(os.environ.get("SHUZI_BROWSER_CONCURRENCY", "2")))
_BROWSER_SEMAPHORE = threading.BoundedSemaphore(_BROWSER_CONCURRENCY)


def _render_page_parts(url: str, timeout: int) -> tuple[str, str]:
    from playwright.sync_api import sync_playwright

    page_url = remove_fragment(url)
    with _BROWSER_SEMAPHORE, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                ignore_https_errors=True,
                user_agent=HEADERS["User-Agent"],
                locale="zh-CN",
            )
            page = context.new_page()
            page.goto(page_url, wait_until="networkidle", timeout=timeout)
            page.wait_for_timeout(1000)
            html_text = page.content()
            try:
                body_text = page.locator("body").inner_text(timeout=3000)
            except Exception:
                body_text = ""
            return body_text, html_text
        finally:
            browser.close()


def render_page_documents(url: str, timeout: int = 25000) -> list[SourceDocument]:
    page_url = remove_fragment(url)
    body_text, html_text = _render_page_parts(url, timeout)
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
                    metadata={"parseable": True},
                )
            )
    return documents
