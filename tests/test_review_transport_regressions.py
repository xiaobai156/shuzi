import gzip
import io
import socket
import ssl
from concurrent.futures import ThreadPoolExecutor
from threading import get_ident, Lock
from urllib.error import HTTPError
from urllib.request import Request
import pytest

from kill_numbers.acquisition import policy, http_client, browser_pool, discovery
from kill_numbers.application.batch_service import iter_completed_batch


def public_dns(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))])


@pytest.mark.parametrize('url',[
    'file:///etc/passwd','ftp://a.test/file','http://localhost','http://a.localhost',
    'http://127.0.0.1','http://10.0.0.1','http://172.16.0.1','http://192.168.1.1',
    'http://169.254.169.254','http://[::1]','http://[fc00::1]','http://user:pass@a.test',
])
def test_private_and_non_http_destinations_are_rejected(url):
    with pytest.raises(ValueError):policy.validate_request_url(url,resolve=False)


def test_dns_private_address_is_rejected_before_transport(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))])
    with pytest.raises(ValueError,match='DNS'):policy.validate_request_url('https://a.test')


def test_script_path_only_authorizes_direct_content_script_discovery():
    assert discovery.is_fetchable_script('https://evil.test/upload/script/x.js','https://a.test')
    assert not discovery.is_fetchable_script('https://evil.test/static/x.js','https://a.test')
    assert discovery.iframe_urls('<iframe src="https://evil.test/upload/script/x.js"></iframe>','https://a.test')==[]
    with policy.target_policy({'url':'https://a.test','allowed_resource_hosts':['cdn.test']}):
        assert discovery.is_fetchable_script('https://cdn.test/x.js','https://a.test')
        assert not discovery.is_fetchable_script('https://cdn.test.evil.test/x.js','https://a.test')


def test_default_tls_verified_and_insecure_setting_is_scoped():
    assert http_client.SSL_CONTEXT.verify_mode==ssl.CERT_REQUIRED
    assert http_client.SSL_CONTEXT.check_hostname
    with policy.target_policy({'url':'https://a.test','insecure_tls':True}):
        assert policy.insecure_for('https://a.test/data')
        assert not policy.insecure_for('https://other.test')
    assert not policy.insecure_for('https://a.test')


def test_redirect_policy_rejects_cross_origin_and_downgrade(monkeypatch):
    public_dns(monkeypatch)
    handler=http_client.SafeRedirectHandler();request=Request('https://a.test/x')
    with policy.target_policy({'url':'https://a.test'}):
        for url in ('https://evil.test/x','http://a.test/x','http://127.0.0.1'):
            with pytest.raises(ValueError):handler.redirect_request(request,None,302,'Found',{},url)
        result=handler.redirect_request(request,None,302,'Found',{},'https://a.test/y')
        assert result.full_url=='https://a.test/y'


@pytest.mark.parametrize('code,attempts',[(404,1),(401,1),(403,1),(502,2),(503,2)])
def test_only_transient_http_errors_receive_bounded_retries(monkeypatch,code,attempts):
    public_dns(monkeypatch);calls=[]
    def failure(req,**kw):
        calls.append(req.full_url);raise HTTPError(req.full_url,code,'error',{},None)
    monkeypatch.setattr(http_client,'urlopen',failure)
    monkeypatch.setattr(http_client,'wait_for_host_slot',lambda url:None)
    monkeypatch.setattr(http_client.time,'sleep',lambda seconds:None)
    with pytest.raises(HTTPError):http_client.fetch_bytes('https://a.test')
    assert len(calls)==attempts


def test_certificate_failure_never_retries_or_invokes_curl(monkeypatch):
    public_dns(monkeypatch);calls=[]
    def failure(req,**kw):
        calls.append(req.full_url);raise ssl.SSLCertVerificationError('bad certificate')
    monkeypatch.setattr(http_client,'urlopen',failure)
    monkeypatch.setattr(http_client,'wait_for_host_slot',lambda url:None)
    monkeypatch.setattr(http_client,'curl_fetch_bytes',lambda *a,**k:pytest.fail('curl fallback forbidden'))
    with pytest.raises(ssl.SSLCertVerificationError):http_client.fetch_bytes('https://a.test')
    assert len(calls)==1


def test_oversized_response_and_gzip_expansion_are_rejected(monkeypatch):
    public_dns(monkeypatch);monkeypatch.setattr(http_client,'MAX_RESPONSE_BYTES',16)
    class Response(io.BytesIO):
        def geturl(self):return 'https://a.test'
    monkeypatch.setattr(http_client,'urlopen',lambda *a,**k:Response(b'a'*17))
    monkeypatch.setattr(http_client,'wait_for_host_slot',lambda url:None)
    with pytest.raises(ValueError,match='大小限制'):http_client.fetch_bytes('https://a.test')
    with pytest.raises(ValueError,match='大小限制'):http_client.decode_response(gzip.compress(b'a'*17))


def test_browser_pool_reuses_owner_state_and_never_shares_threads():
    seen={};lock=Lock()
    def render(state,url,timeout):
        ident=get_ident()
        with lock:
            if 'owner' in state:assert state['owner']==ident
            state['owner']=ident;state['calls']=state.get('calls',0)+1
            seen[ident]=state['calls']
        return ident,url,policy.CURRENT_POLICY.get().origins
    pool=browser_pool.BrowserPool(workers=2,renderer=render)
    def invoke(i):
        with policy.target_policy({'url':f'https://site{i}.test'}):
            return pool.render(f'https://site{i}.test',1000)
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            output=list(executor.map(invoke,range(12)))
        assert len(seen)==2 and sum(seen.values())==12
        for _,url,origins in output:assert policy.url_origin(url) in origins
    finally:pool.close()
    assert all(not worker.thread.is_alive() for worker in pool.workers)
    with pytest.raises(RuntimeError):pool.render('https://a.test',1000)


def test_worker_exception_does_not_kill_reusable_browser_owner():
    def render(state,url,timeout):
        if url=='bad':raise ValueError('expected')
        return 'ok'
    pool=browser_pool.BrowserPool(workers=1,renderer=render)
    try:
        with pytest.raises(ValueError):pool.render('bad',1000)
        assert pool.render('good',1000)=='ok'
    finally:pool.close()


def test_keyboard_interrupt_is_not_swallowed_by_batch_boundary():
    def interrupt(item):raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):list(iter_completed_batch([1],interrupt,1))


def test_admin_auth_failure_never_switches_to_another_source(monkeypatch):
    from kill_numbers.acquisition.strategies import admin_article as admin
    def forbidden(url):raise HTTPError(url,403,'Forbidden',{},None)
    monkeypatch.setattr(admin,'fetch_json',forbidden)
    monkeypatch.setattr(admin,'fetch_admin_article_from_landing',lambda *a:pytest.fail('fallback forbidden'))
    with pytest.raises(HTTPError):
        admin.crawl_admin_article_page('https://a.test/article/admin/1?url=site', {}, ['215'],
            content_matches=lambda *a:False,render_page=lambda *a:pytest.fail('browser forbidden'))


def test_populated_admin_article_missing_issue_does_not_search_other_sources(monkeypatch):
    from kill_numbers.acquisition.strategies import admin_article as admin
    monkeypatch.setattr(admin,'fetch_json',lambda url:{'id':'1','title':'214期','content':'214期 专属栏目 01 02 03'})
    monkeypatch.setattr(admin,'fetch_admin_article_from_landing',lambda *a:pytest.fail('fallback forbidden'))
    _,content=admin.crawl_admin_article_page('https://a.test/article/admin/1?url=site',{},['215'],
        content_matches=lambda *a:False,render_page=lambda *a:pytest.fail('browser forbidden'))
    assert '214期' in content and '215期' not in content


def test_direct_public_content_script_is_discoverable_but_arbitrary_cross_origin_is_not(monkeypatch):
    public_dns(monkeypatch)
    page = 'https://a.test/topic/1.html'
    content_script = 'https://xia01.cosds.example/upload/script/09/body.js'
    assert discovery.is_fetchable_script(content_script, page)
    assert not discovery.is_fetchable_script('https://xia01.cosds.example/static/jquery.js', page)
    assert discovery.iframe_urls('<iframe src="https://xia01.cosds.example/upload/script/x.js"></iframe>', page) == []
    assert not policy.child_url_allowed(content_script, page)
    with policy.target_policy({'url': page}):
        assert not policy.child_url_allowed(content_script, page)
        with policy.allow_discovered_child_url(content_script):
            assert policy.child_url_allowed(content_script, page)
        assert not policy.child_url_allowed(content_script, page)


def test_document_write_stream_replays_only_proven_static_calls_in_order():
    import base64
    first = base64.b64encode('作者:甲\n'.encode()).decode()
    second = base64.b64encode('252期 绝杀10码 01 02 03\n'.encode()).decode()
    script = (
        f'document.write(utf8to16(strdecode("{first}")));'
        f'document.writeln(strdecode("{second}"));'
    )
    assert discovery.render_document_write_stream(script) == '作者:甲\n252期 绝杀10码 01 02 03\n\n'
    # Raw independent decoder calls are observations, not browser write semantics.
    assert discovery.render_document_write_stream(
        f'const a=strdecode("{first}"); const b=strdecode("{second}");'
    ) is None
    assert discovery.render_document_write_stream('document.write(getRemoteValue())') is None


def test_content_script_tls_fallback_only_for_incomplete_chain_and_matching_leaf(monkeypatch):
    from urllib.error import URLError

    public_dns(monkeypatch)
    monkeypatch.setattr(http_client, 'wait_for_host_slot', lambda url: None)
    calls = []

    class Headers(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    class Response(io.BytesIO):
        headers = Headers({'Content-Type': 'application/javascript'})
        def geturl(self):
            return 'https://cdn.test/upload/script/a.js'
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    def fake_urlopen(req, **kwargs):
        calls.append(kwargs['context'].verify_mode)
        if len(calls) == 1:
            raise URLError(ssl.SSLCertVerificationError('unable to get local issuer certificate'))
        return Response(b'document.write("ok")')

    monkeypatch.setattr(http_client, 'urlopen', fake_urlopen)
    monkeypatch.setattr(http_client, '_peer_certificate_matches_hostname', lambda *a, **k: True)
    with policy.target_policy({'url': 'https://page.test/topic/1'}):
        text, bypassed = http_client.fetch_content_script_text(
            'https://cdn.test/upload/script/a.js',
            'https://page.test/topic/1',
        )
    assert text == 'document.write("ok")'
    assert bypassed is True
    assert calls == [ssl.CERT_REQUIRED, ssl.CERT_NONE]


def test_content_script_hostname_mismatch_or_other_tls_failure_never_bypasses(monkeypatch):
    from urllib.error import URLError

    public_dns(monkeypatch)
    monkeypatch.setattr(http_client, 'wait_for_host_slot', lambda url: None)
    leaf_calls = []

    def incomplete(req, **kwargs):
        raise URLError(ssl.SSLCertVerificationError('unable to verify the first certificate'))

    monkeypatch.setattr(http_client, 'urlopen', incomplete)
    monkeypatch.setattr(
        http_client,
        '_peer_certificate_matches_hostname',
        lambda *a, **k: leaf_calls.append(1) or False,
    )
    with policy.target_policy({'url': 'https://page.test/topic/1'}):
        with pytest.raises(ValueError, match='主机名不匹配'):
            http_client.fetch_content_script_text(
                'https://cdn.test/upload/script/a.js',
                'https://page.test/topic/1',
            )
    assert leaf_calls == [1]

    leaf_calls.clear()
    def hostname_failure(req, **kwargs):
        raise URLError(ssl.SSLCertVerificationError('hostname mismatch'))
    monkeypatch.setattr(http_client, 'urlopen', hostname_failure)
    with policy.target_policy({'url': 'https://page.test/topic/1'}):
        with pytest.raises(URLError):
            http_client.fetch_content_script_text(
                'https://cdn.test/upload/script/a.js',
                'https://page.test/topic/1',
            )
    assert leaf_calls == []


def test_leaf_certificate_wildcard_match_is_one_label_only():
    cert = {'subjectAltName': (('DNS', '*.cosds.aohjifv.com'), ('DNS', 'cosds.aohjifv.com'))}
    assert http_client.certificate_dict_matches_hostname(cert, 'xia01.cosds.aohjifv.com')
    assert http_client.certificate_dict_matches_hostname(cert, 'cosds.aohjifv.com')
    assert not http_client.certificate_dict_matches_hostname(cert, 'a.b.cosds.aohjifv.com')
    assert not http_client.certificate_dict_matches_hostname(cert, 'evil-aohjifv.com')


def test_rendered_script_page_preserves_real_script_tag_order(monkeypatch):
    from kill_numbers.acquisition import documents
    import base64

    page = 'https://page.test/topic/1'
    one = 'https://cdn.test/upload/script/one.js'
    two = 'https://cdn.test/upload/script/two.js'
    a = base64.b64encode('<div class="topic-content">作者:甲\n'.encode()).decode()
    b = base64.b64encode('252期 绝杀10码【01 02 03】</div>'.encode()).decode()
    raw = f'<html><script src="{one}"></script><span>中间</span><script src="{two}"></script></html>'
    scripts = {
        one: f'document.write(strdecode("{a}"));',
        two: f'document.write(strdecode("{b}"));',
    }
    monkeypatch.setattr(documents, 'fetch_text', lambda url: raw if url == page else pytest.fail(url))
    monkeypatch.setattr(
        documents,
        'fetch_content_script_text',
        lambda url, parent_url: (scripts[url], True),
    )
    public_dns(monkeypatch)
    with policy.target_policy({'url': page}):
        _name, found = documents.discover_static_documents(page)
    rendered = [item for item in found if item.kind == 'rendered_script_page']
    assert len(rendered) == 1
    assert '作者:甲' in rendered[0].content
    assert '中间' in rendered[0].content
    assert '252期' in rendered[0].content
    assert rendered[0].content.index('作者:甲') < rendered[0].content.index('中间') < rendered[0].content.index('252期')
    assert rendered[0].metadata['tls_chain_unverified_scripts'] == 2


def test_rendered_script_page_is_not_emitted_when_one_content_script_is_unresolved(monkeypatch):
    from kill_numbers.acquisition import documents

    page = 'https://page.test/topic/1'
    one = 'https://cdn.test/upload/script/one.js'
    two = 'https://cdn.test/upload/script/two.js'
    raw = f'<script src="{one}"></script><script src="{two}"></script>'
    scripts = {one: 'document.write("one")', two: 'document.write(dynamic())'}
    monkeypatch.setattr(documents, 'fetch_text', lambda url: raw if url == page else pytest.fail(url))
    monkeypatch.setattr(documents, 'fetch_content_script_text', lambda url, parent_url: (scripts[url], False))
    public_dns(monkeypatch)
    with policy.target_policy({'url': page}):
        _name, found = documents.discover_static_documents(page)
    assert not [item for item in found if item.kind == 'rendered_script_page']
