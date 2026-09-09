"""Bounded, reusable browsers. Every sync Playwright object stays on its owner thread."""
import atexit
import os
import time
from concurrent.futures import Future, TimeoutError as FutureTimeout
from contextvars import copy_context
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread

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
            page.wait_for_function(r'''({anchors, issues, keywords, expectedCount, history, historyDepth}) => {
                if (!document.body) return false;
                const raw = document.body.innerText || '';
                const clean = s => s.normalize('NFKC').replace(/\s+/g, '');
                const body = clean(raw);
                const anchorReady = !anchors.length || anchors.some(a => body.includes(clean(a)));
                const issueReady = !issues.length || issues.every(i =>
                    new RegExp('(^|[^0-9])0?' + i + '期').test(body));
                const keywordReady = !keywords.length || keywords.some(k => body.includes(clean(k)));
                let discoveredPeriods = 0;
                if (history) {
                    const rows = raw.split(/\r?\n/);
                    const found = new Set();
                    for (let index = 0; index < rows.length; index += 1) {
                        const sample = rows.slice(index, index + 4).join(' ');
                        const sampleClean = clean(sample);
                        const issueMatch = sample.match(/(^|[^0-9])0?([0-9]{1,3})\s*期/);
                        if (!issueMatch) continue;
                        if (keywords.length && !keywords.some(k => sampleClean.includes(clean(k)))) {
                            continue;
                        }
                        const issueEnd = (issueMatch.index || 0) + issueMatch[0].length;
                        let candidateBody = sample.slice(issueEnd);
                        const nextIssue = candidateBody.search(/(^|[^0-9])0?[0-9]{1,3}\s*期/);
                        if (nextIssue >= 0) candidateBody = candidateBody.slice(0, nextIssue);
                        candidateBody = candidateBody.split(/[=＝]?\s*开\s*[:：]?/, 1)[0];
                        const values = candidateBody.match(/(^|[^0-9])([0-9]{2})(?=[^0-9]|$)/g) || [];
                        if (expectedCount && values.length !== expectedCount) continue;
                        found.add(String(Number(issueMatch[2])));
                    }
                    discoveredPeriods = found.size;
                }
                const historyReady = !history || discoveredPeriods >= 1;
                const contentReady = history
                    ? anchorReady && keywordReady && historyReady
                    : anchorReady && issueReady;
                if (!contentReady) {
                    window.__shuziReadyProbe = null;
                    return false;
                }
                const signature = body.length + ':' + body.slice(-1024);
                const now = Date.now();
                const previous = window.__shuziReadyProbe;
                if (!previous || previous.signature !== signature) {
                    window.__shuziReadyProbe = {signature, since: now};
                    return false;
                }
                const desiredDepth = Math.max(1, historyDepth || 1);
                const stableFor = history && discoveredPeriods < desiredDepth ? 1500 : 500;
                return now - previous.since >= stableFor;
            }''', arg={
                'anchors': list(policy.anchors),
                'issues': list(policy.issues),
                'keywords': list(policy.keywords),
                'expectedCount': policy.expected_count,
                'history': policy.history_discovery or not policy.issues,
                'historyDepth': policy.history_depth,
            }, timeout=timeout)
        validate_request_url(page.url)
        return page.locator('body').inner_text(), page.content(), page.url
    finally:
        context.close()


class BrowserWorker:
    def __init__(
        self,
        renderer=_render_owned,
        *,
        queue_size: int = 32,
        close_timeout: float = 15.0,
    ):
        self.queue = Queue(maxsize=queue_size)
        self.renderer = renderer
        self.close_timeout = close_timeout
        self.retired = Event()
        self.thread = Thread(
            target=self._run,
            name='shuzi-browser-owner',
            daemon=True,
        )
        self.thread.start()

    def _fail_pending(self, exc):
        while True:
            try:
                item = self.queue.get_nowait()
            except Empty:
                return
            if item is None:
                continue
            _context, _url, _timeout, future = item
            if not future.done():
                future.set_exception(exc)

    def _run(self):
        state = {}
        terminal_error = RuntimeError('浏览器工作线程已停止')
        try:
            while not self.retired.is_set():
                item = self.queue.get()
                if item is None:
                    break
                context, url, timeout, future = item
                try:
                    result = context.run(self.renderer, state, url, timeout)
                except BaseException as exc:
                    if not future.done():
                        future.set_exception(exc)
                    if not isinstance(exc, Exception):
                        terminal_error = RuntimeError(
                            f'浏览器工作线程异常终止：{type(exc).__name__}'
                        )
                        break
                else:
                    if not future.done():
                        future.set_result(result)
        finally:
            self.retired.set()
            self._fail_pending(terminal_error)
            try:
                if state.get('browser'):
                    state['browser'].close()
            finally:
                if state.get('playwright'):
                    state['playwright'].stop()

    def submit(self, url, timeout):
        if self.retired.is_set() or not self.thread.is_alive():
            raise RuntimeError('浏览器工作线程不可用')
        future = Future()
        try:
            self.queue.put(
                (copy_context(), url, timeout, future),
                timeout=max(0.1, timeout / 1000),
            )
        except Full as exc:
            raise RuntimeError('浏览器任务队列已满') from exc
        return future

    def retire(self):
        self.retired.set()
        try:
            self.queue.put_nowait(None)
        except Full:
            pass

    def close(self, timeout: float | None = None):
        self.retire()
        wait = self.close_timeout if timeout is None else max(0.0, timeout)
        self.thread.join(timeout=wait)


class BrowserPool:
    def __init__(
        self,
        workers=BROWSER_WORKERS,
        renderer=_render_owned,
        *,
        operation_grace: float = 10.0,
        close_timeout: float = 15.0,
    ):
        if type(workers) is not int or not 1 <= workers <= 4:
            raise ValueError('浏览器并发必须是1至4的整数')
        self.renderer = renderer
        self.operation_grace = operation_grace
        self.close_timeout = close_timeout
        self.workers = [self._new_worker() for _ in range(workers)]
        self.retired_workers = []
        self.lock = Lock()
        self.next_worker = 0
        self.closed = False

    def _new_worker(self):
        return BrowserWorker(
            self.renderer,
            close_timeout=self.close_timeout,
        )

    def _replace_worker(self, worker):
        with self.lock:
            if self.closed:
                return
            try:
                index = self.workers.index(worker)
            except ValueError:
                return
            self.workers[index] = self._new_worker()
            self.retired_workers = [
                item for item in self.retired_workers if item.thread.is_alive()
            ]
            self.retired_workers.append(worker)
        worker.retire()

    def render(self, url, timeout):
        if type(timeout) is not int or timeout <= 0:
            raise ValueError('浏览器超时必须是正整数毫秒')
        with self.lock:
            if self.closed:
                raise RuntimeError('浏览器池已关闭')
            worker = self.workers[self.next_worker % len(self.workers)]
            self.next_worker += 1
            if not worker.thread.is_alive() or worker.retired.is_set():
                index = self.workers.index(worker)
                old_worker = worker
                worker = self._new_worker()
                self.workers[index] = worker
                self.retired_workers.append(old_worker)
                old_worker.retire()
            future = worker.submit(url, timeout)
        try:
            return future.result(timeout=(timeout / 1000) + self.operation_grace)
        except FutureTimeout as exc:
            self._replace_worker(worker)
            raise TimeoutError(
                f'浏览器渲染超过 {timeout}ms 且未能安全结束'
            ) from exc
        except BaseException:
            if not worker.thread.is_alive():
                self._replace_worker(worker)
            raise

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            workers = list(dict.fromkeys(self.workers + self.retired_workers))
        deadline = time.monotonic() + self.close_timeout
        for worker in workers:
            worker.retire()
        for worker in workers:
            worker.close(timeout=max(0.0, deadline - time.monotonic()))


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
