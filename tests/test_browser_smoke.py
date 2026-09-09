"""Optional real Chromium smoke test against an isolated local HTTP fixture only."""
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from concurrent.futures import ThreadPoolExecutor
import pytest
from kill_numbers.acquisition import browser_pool
from kill_numbers.acquisition.policy import target_policy


@pytest.mark.skipif(os.environ.get('RUN_BROWSER_SMOKE') != '1', reason='Enable RUN_BROWSER_SMOKE with Playwright Chromium installed')
def test_real_chromium_owner_threads_and_context_isolation(monkeypatch):
    pytest.importorskip('playwright.sync_api')
    cookies=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/":
                self.send_response(404);self.end_headers();return
            cookies.append(self.headers.get('Cookie'))
            body='<meta charset="utf-8"><h1>专 属 栏 目</h1><p>215期 01 02 03</p>'.encode()
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Set-Cookie','private=value; Path=/')
            self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    # Deliberate test-only loopback exception. Production URL checks stay strict.
    monkeypatch.setattr(browser_pool,'validate_request_url',lambda url:url)
    pool=browser_pool.BrowserPool(workers=2)
    url=f'http://127.0.0.1:{server.server_port}/'
    def render(_):
        with target_policy({'url':url,'anchor':'专属栏目'},['215']):
            return pool.render(url,10000)
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            results=list(executor.map(render,range(4)))
        assert all('215期 01 02 03' in body and final==url for body,html,final in results)
        assert cookies and all(cookie is None for cookie in cookies)
    finally:
        pool.close();server.shutdown();server.server_close();thread.join()
    assert all(not worker.thread.is_alive() for worker in pool.workers)


@pytest.mark.skipif(os.environ.get('RUN_BROWSER_SMOKE') != '1', reason='Enable RUN_BROWSER_SMOKE with Playwright Chromium installed')
def test_history_discovery_waits_for_delayed_data_rows(monkeypatch):
    pytest.importorskip('playwright.sync_api')

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = '''<meta charset="utf-8">
<h1 id="anchor">专属栏目</h1>
<div id="rows"></div>
<script>
const incomplete = [215,214,213,212,211,210,209,208,207,206]
  .map((issue, index) => `${issue}期 专属栏目 ${String(index * 2 + 1).padStart(2, '0')} ${String(index * 2 + 2).padStart(2, '0')}`)
  .join('\\n');
setTimeout(() => {
  document.getElementById('rows').textContent = incomplete;
}, 250);
setTimeout(() => {
  document.getElementById('rows').textContent = [
    '215期 专属栏目 01 02 03',
    '214期 专属栏目 04 05 06',
    '213期 专属栏目 07 08 09',
    '212期 专属栏目 10 11 12',
    '211期 专属栏目 13 14 15',
    '210期 专属栏目 16 17 18',
    '209期 专属栏目 19 20 21',
    '208期 专属栏目 22 23 24',
    '207期 专属栏目 25 26 27',
    '206期 专属栏目 28 29 30',
  ].join('\\n');
}, 900);
</script>'''.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(browser_pool, 'validate_request_url', lambda url: url)
    pool = browser_pool.BrowserPool(workers=1)
    url = f'http://127.0.0.1:{server.server_port}/'
    try:
        with target_policy({
            'url': url,
            'anchor': '专属栏目',
            'keywords': ['专属栏目'],
            'count': 3,
            '_history_discovery': True,
            '_history_depth': 10,
        }, []):
            body, _html, final = pool.render(url, 10000)
        assert '215期 专属栏目 01 02 03' in body
        assert final == url
    finally:
        pool.close()
        server.shutdown()
        server.server_close()
        thread.join()
