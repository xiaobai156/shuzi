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


def test_only_direct_public_upload_scripts_get_narrow_cross_origin_exception(monkeypatch):
    monkeypatch.setattr(
        discovery,
        'validate_public_request_url',
        lambda url: url if url.startswith('https://cdn.test/') else (_ for _ in ()).throw(ValueError('blocked')),
    )
    assert discovery.is_fetchable_script(
        'https://cdn.test/upload/script/x.js', 'https://a.test'
    )
    assert not discovery.is_fetchable_script(
        'https://cdn.test/static/jquery.js', 'https://a.test'
    )
    assert not discovery.is_fetchable_script(
        'https://evil.test/upload/script/x.js', 'https://a.test'
    )
    assert discovery.iframe_urls(
        '<iframe src="https://cdn.test/x"></iframe>', 'https://a.test'
    ) == []
    with policy.target_policy({'url':'https://a.test','allowed_resource_hosts':['cdn.test']}):
        assert discovery.is_fetchable_script('https://cdn.test/static/x.js','https://a.test')
        assert not discovery.is_fetchable_script('https://cdn.test.evil.test/static/x.js','https://a.test')


def test_discovered_child_host_scope_is_temporary(monkeypatch):
    public_dns(monkeypatch)
    with policy.target_policy({'url': 'https://a.test'}):
        with pytest.raises(ValueError, match='跨域'):
            policy.validate_request_url('https://cdn.test/upload/script/x.js', resolve=False)
        with policy.allow_discovered_child_host('https://cdn.test/upload/script/x.js'):
            assert policy.validate_request_url(
                'https://cdn.test/upload/script/x.js', resolve=False
            )
            with pytest.raises(ValueError, match='跨域'):
                policy.validate_request_url('https://other.test/x.js', resolve=False)
        with pytest.raises(ValueError, match='跨域'):
            policy.validate_request_url('https://cdn.test/upload/script/x.js', resolve=False)


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
