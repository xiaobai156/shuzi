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
            body='<meta charset="utf-8"><h1>专属栏目</h1><p>215期 01 02 03</p>'.encode()
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
