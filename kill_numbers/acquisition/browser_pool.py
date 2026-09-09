"""Bounded, reusable browsers. Every sync Playwright object stays on its owner thread."""
import atexit
import os
from concurrent.futures import Future
from contextvars import copy_context
from queue import Queue
from threading import Lock, Thread

from kill_numbers.acquisition.http_client import HEADERS
from kill_numbers.acquisition.documents import make_source_document
from kill_numbers.acquisition.discovery import decode_strdecode_payloads
from kill_numbers.acquisition.policy import CURRENT_POLICY, validate_request_url, insecure_for
from kill_numbers.text_utils import remove_fragment


def configured_browser_workers():
    try:
        value = int(os.environ.get('SHUZI_BROWSER_CONCURRENCY', '2'))
    except ValueError as exc:
        raise ValueError('SHUZI_BROWSER_CONCURRENCY 必须是1至4的整数') from exc
    if not 1 <= value <= 4:
        raise ValueError('SHUZI_BROWSER_CONCURRENCY 必须是1至4的整数')
    return value


BROWSER_WORKERS = configured_browser_workers()
_POOL = None
_POOL_LOCK = Lock()


def _render_owned(state, url, timeout):
    validate_request_url(url)
    if state.get('browser') is None or not state['browser'].is_connected():
        if state.get('playwright') is None:
            from playwright.sync_api import sync_playwright
            state['playwright'] = sync_playwright().start()
        state['browser'] = state['playwright'].chromium.launch(headless=True, timeout=15000)
    context = state['browser'].new_context(ignore_https_errors=insecure_for(url),
        user_agent=HEADERS['User-Agent'], locale='zh-CN', service_workers='block')
    try:
        def route_request(route):
            try:
                if route.request.resource_type in {'image', 'media', 'font'}:
                    route.abort()
                    return
                validate_request_url(route.request.url)
            except (ValueError, OSError):
                route.abort()
                return
            route.continue_()
        context.route('**/*', route_request)
        page = context.new_page()
        page.set_default_timeout(timeout)
        page.goto(remove_fragment(url), wait_until='domcontentloaded', timeout=timeout)
        policy = CURRENT_POLICY.get()
        if policy.ready_selector:
            page.locator(policy.ready_selector).first.wait_for(state='attached', timeout=timeout)
        else:
            page.wait_for_function(r'''({anchors, issues}) => {
                if (!document.body) return false;
                const clean = s => s.normalize('NFKC').replace(/\s+/g, '');
                const body = clean(document.body.innerText || '');
                return (!anchors.length || anchors.some(a => body.includes(clean(a))))
                    && issues.every(i => new RegExp('(^|[^0-9])0?' + i + '期').test(body));
            }''', arg={'anchors':list(policy.anchors), 'issues':list(policy.issues)}, timeout=timeout)
        validate_request_url(page.url)
        return page.locator('body').inner_text(), page.content(), page.url
    finally:
        context.close()


class BrowserWorker:
    def __init__(self, renderer=_render_owned):
        self.queue = Queue(maxsize=32)
        self.renderer = renderer
        self.thread = Thread(target=self._run, name='shuzi-browser-owner', daemon=True)
        self.thread.start()

    def _run(self):
        state = {}
        try:
            while True:
                item = self.queue.get()
                if item is None:
                    break
                context, url, timeout, future = item
                try:
                    future.set_result(context.run(self.renderer, state, url, timeout))
                except Exception as exc:
                    future.set_exception(exc)
        finally:
            try:
                if state.get('browser'):
                    state['browser'].close()
            finally:
                if state.get('playwright'):
                    state['playwright'].stop()

    def submit(self, url, timeout):
        future = Future()
        self.queue.put((copy_context(), url, timeout, future), timeout=timeout/1000)
        return future

    def close(self):
        self.queue.put(None)
        self.thread.join()


class BrowserPool:
    def __init__(self, workers=BROWSER_WORKERS, renderer=_render_owned):
        if type(workers) is not int or workers < 1:
            raise ValueError('浏览器并发必须是正整数')
        self.workers = [BrowserWorker(renderer) for _ in range(workers)]
        self.lock = Lock()
        self.next_worker = 0
        self.closed = False

    def render(self, url, timeout):
        with self.lock:
            if self.closed:
                raise RuntimeError('浏览器池已关闭')
            worker = self.workers[self.next_worker % len(self.workers)]
            self.next_worker += 1
            future = worker.submit(url, timeout)
        # Playwright owns operation timeouts. Do not cancel its API call from
        # another thread (cancelling an in-flight Playwright task is unsafe).
        return future.result()

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
        for worker in self.workers:
            worker.close()


def _render_page_parts(url, timeout):
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = BrowserPool()
        pool = _POOL
    return pool.render(url, timeout)


def close_browser_pool():
    global _POOL
    with _POOL_LOCK:
        pool, _POOL = _POOL, None
    if pool:
        pool.close()


atexit.register(close_browser_pool)


def render_page_documents(url: str, timeout: int = 25000):
    body_text, html_text, final_url = _render_page_parts(url, timeout)
    documents = []
    for kind, content, priority in [('browser_body', body_text, 60), ('browser_dom', html_text, 50)]:
        if content:
            documents.append(make_source_document(kind=kind, url=final_url,
                parent_url=url, content=content, priority=priority, metadata={'parseable':True}))
    for index, value in enumerate(decode_strdecode_payloads(html_text)):
        documents.append(make_source_document(kind='browser_decoded', url=f'{final_url}#browser-decoded-{index+1}',
            parent_url=final_url, content=value, priority=45, metadata={'parseable':True}))
    return documents
