import gzip
import io
import json
import random
import re
import shutil
import socket
import ssl
import tempfile
import os
import subprocess
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler
from kill_numbers.acquisition.policy import (
    allow_discovered_child_url,
    validate_request_url,
    insecure_for,
)

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_request_url(newurl)
        if req.full_url.startswith("https://") and not newurl.startswith("https://"):
            raise ValueError("拒绝 HTTPS 降级重定向")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def urlopen(request, *, timeout, context, redirect_handler=None):
    opener = build_opener(
        HTTPSHandler(context=context),
        redirect_handler or SafeRedirectHandler(),
    )
    return opener.open(request, timeout=timeout)



class SameHostHTTPSRedirectHandler(SafeRedirectHandler):
    def __init__(self, expected_host: str):
        super().__init__()
        self.expected_host = expected_host.rstrip('.').lower()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if parsed.scheme.lower() != 'https' or (parsed.hostname or '').rstrip('.').lower() != self.expected_host:
            raise ValueError('正文脚本重定向离开原HTTPS主机，已拒绝')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _exception_chain(exc):
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = getattr(current, 'reason', None) or getattr(current, '__cause__', None)


def is_incomplete_tls_chain_error(exc) -> bool:
    markers = (
        'unable to get local issuer certificate',
        'unable to verify the first certificate',
    )
    return any(
        any(marker in str(item).lower() for marker in markers)
        for item in _exception_chain(exc)
    )


def _dns_pattern_matches(pattern: str, hostname: str) -> bool:
    pattern = pattern.rstrip('.').lower()
    hostname = hostname.rstrip('.').lower()
    if pattern.startswith('*.'):
        suffix = pattern[1:]
        return hostname.endswith(suffix) and hostname.count('.') == pattern.count('.')
    return pattern == hostname


def certificate_dict_matches_hostname(cert: dict, hostname: str) -> bool:
    dns_names = [
        value
        for kind, value in cert.get('subjectAltName', ())
        if kind == 'DNS'
    ]
    if dns_names:
        return any(_dns_pattern_matches(pattern, hostname) for pattern in dns_names)
    common_names = [
        value
        for rdn in cert.get('subject', ())
        for kind, value in rdn
        if kind == 'commonName'
    ]
    return any(_dns_pattern_matches(pattern, hostname) for pattern in common_names)


def _peer_certificate_matches_hostname(url: str, timeout: int) -> bool:
    validate_request_url(url)
    parsed = urlparse(url)
    if parsed.scheme.lower() != 'https' or not parsed.hostname:
        return False
    host = parsed.hostname.rstrip('.').lower()
    port = parsed.port or 443
    context = ssl._create_unverified_context()
    with socket.create_connection((host, port), timeout=min(timeout, 10)) as raw_socket:
        with context.wrap_socket(raw_socket, server_hostname=host) as tls_socket:
            der = tls_socket.getpeercert(binary_form=True)
    if not der:
        return False
    fd, cert_path = tempfile.mkstemp(prefix='shuzi-cert-', suffix='.pem')
    try:
        with os.fdopen(fd, 'wb') as cert_file:
            cert_file.write(ssl.DER_cert_to_PEM_cert(der).encode('ascii'))
        fd = -1
        cert = ssl._ssl._test_decode_cert(cert_path)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(cert_path)
        except OSError:
            pass
    return certificate_dict_matches_hostname(cert, host)


def _content_script_once(url: str, parent_url: str, timeout: int, context: ssl.SSLContext) -> bytes:
    parsed = urlparse(url)
    host = (parsed.hostname or '').rstrip('.').lower()
    if parsed.scheme.lower() != 'https' or not host:
        raise ValueError('正文脚本窄范围链修复只允许HTTPS')
    headers = {
        **HEADERS,
        'Referer': parent_url,
        'Accept': 'application/javascript,text/javascript,application/x-javascript,*/*;q=0.1',
    }
    wait_for_host_slot(url)
    with urlopen(
        Request(url, headers=headers),
        timeout=timeout,
        context=context,
        redirect_handler=SameHostHTTPSRedirectHandler(host),
    ) as response:
        final_url = response.geturl()
        validate_request_url(final_url)
        final = urlparse(final_url)
        if final.scheme.lower() != 'https' or (final.hostname or '').rstrip('.').lower() != host:
            raise ValueError('正文脚本最终URL离开原HTTPS主机，已拒绝')
        content_type = str(response.headers.get('Content-Type', '')).lower().split(';', 1)[0].strip()
        if 'javascript' not in content_type:
            raise ValueError(f'正文脚本Content-Type异常：{content_type or "缺失"}')
        data = response.read(MAX_RESPONSE_BYTES + 1)
        if len(data) > MAX_RESPONSE_BYTES:
            raise ValueError('响应超过大小限制')
        return data


def fetch_content_script_text(url: str, parent_url: str, timeout: int = 25) -> tuple[str, bool]:
    """Fetch one direct content script with a tightly scoped chain-only fallback.

    Normal certificate verification is always attempted first.  A second
    unverified-chain request is permitted only for the two explicit incomplete
    chain errors and only after the leaf certificate SAN/CN matches the exact
    requested hostname.  Hostname mismatch never enters this path.
    """
    with allow_discovered_child_url(url):
        try:
            data = _content_script_once(url, parent_url, timeout, SSL_CONTEXT)
            return decode_response(data), False
        except Exception as exc:
            if not is_incomplete_tls_chain_error(exc):
                raise
            if not _peer_certificate_matches_hostname(url, timeout):
                raise ValueError('正文脚本证书链不完整且叶子证书主机名不匹配，已拒绝') from exc
            data = _content_script_once(
                url,
                parent_url,
                timeout,
                ssl._create_unverified_context(),
            )
            return decode_response(data), True


REQUEST_RETRIES = 2
HOST_MIN_INTERVAL = 0.7
TRANSIENT_ERROR_MARKERS = (
    "WinError 10054",
    "WinError 10053",
    "UNEXPECTED_EOF_WHILE_READING",
    "EOF occurred in violation of protocol",
    "RemoteDisconnected",
    "Connection reset",
    "timed out",
    "HTTP Error 502",
    "HTTP Error 503",
    "HTTP Error 504",
    "Bad Gateway",
    "curl 连接失败",
    "curl exit 35",
    "curl: (35)",
    "schannel",
    "SSL/TLS connection failed",
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,text/plain,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "identity",
    "Connection": "close",
}

_HOST_LOCKS: dict[str, threading.Lock] = {}
_HOST_LAST_REQUEST: dict[str, float] = {}
_HOST_LOCKS_GUARD = threading.Lock()


def create_ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context()


SSL_CONTEXT = create_ssl_context()


def host_key(url: str) -> str:
    return urlparse(url).netloc.lower()


def host_lock(url: str) -> threading.Lock:
    key = host_key(url)
    with _HOST_LOCKS_GUARD:
        lock = _HOST_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _HOST_LOCKS[key] = lock
        return lock


def wait_for_host_slot(url: str) -> None:
    key = host_key(url)
    with host_lock(url):
        elapsed = time.monotonic() - _HOST_LAST_REQUEST.get(key, 0.0)
        wait_time = HOST_MIN_INTERVAL - elapsed
        if wait_time > 0:
            time.sleep(wait_time + random.uniform(0.05, 0.25))
        _HOST_LAST_REQUEST[key] = time.monotonic()


def is_retryable_network_error(exc: Exception | str) -> bool:
    text = str(exc)
    return any(marker in text for marker in TRANSIENT_ERROR_MARKERS)


def curl_fetch_bytes(url: str, timeout: int = 25) -> bytes:
    validate_request_url(url)
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise RuntimeError("curl 不可用")
    # Never let curl follow a redirect outside the Python URL policy.
    cmd = [curl, "--fail", "--silent", "--show-error", "--location", "--max-redirs", "0",
           "--proto", "=http,https", "--connect-timeout", str(min(15, timeout)),
           "--max-time", str(timeout), "--max-filesize", str(MAX_RESPONSE_BYTES),
           "-A", HEADERS["User-Agent"], "-H", "Accept-Encoding: identity"]
    if insecure_for(url):
        cmd.append("--insecure")
    cmd.append(url)
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout+5)
    if proc.returncode:
        raise RuntimeError(f"curl exit {proc.returncode}: {proc.stderr.decode('utf-8', errors='replace')[:300]}")
    if len(proc.stdout) > MAX_RESPONSE_BYTES:
        raise ValueError("响应超过大小限制")
    return proc.stdout


def fetch_bytes(url: str, timeout: int = 25) -> bytes:
    last_error = None
    for attempt in range(REQUEST_RETRIES):
        try:
            validate_request_url(url)
            wait_for_host_slot(url)
            context = ssl._create_unverified_context() if insecure_for(url) else SSL_CONTEXT
            with urlopen(Request(url, headers=HEADERS), timeout=timeout, context=context) as response:
                validate_request_url(response.geturl())
                data = response.read(MAX_RESPONSE_BYTES + 1)
                if len(data) > MAX_RESPONSE_BYTES:
                    raise ValueError("响应超过大小限制")
                return data
        except HTTPError as exc:
            if exc.code not in (408, 429, 500, 502, 503, 504):
                raise
            last_error = exc
        except (ssl.SSLCertVerificationError, ValueError):
            raise
        except Exception as exc:
            if not is_retryable_network_error(exc):
                raise
            last_error = exc
        if attempt + 1 < REQUEST_RETRIES:
            time.sleep(1.0)
    raise last_error


def decode_response(data: bytes) -> str:
    if not data:
        return ""
    if data.startswith(b"\x1f\x8b"):
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            data = stream.read(MAX_RESPONSE_BYTES + 1)
        if len(data) > MAX_RESPONSE_BYTES:
            raise ValueError("解压后的响应超过大小限制")
    head = data[:3000].decode("ascii", errors="ignore")
    meta = re.search(r"charset=[\"']?([A-Za-z0-9_-]+)", head, re.I)
    encodings = []
    if meta:
        encodings.append(meta.group(1))
    encodings += ["utf-8", "gb18030", "big5", "latin1"]
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="ignore")


def fetch_text(url: str, timeout: int = 25) -> str:
    return decode_response(fetch_bytes(url, timeout=timeout))


def fetch_json(url: str):
    return json.loads(fetch_text(url))


def is_http_404(exc: Exception) -> bool:
    return isinstance(exc, HTTPError) and exc.code == 404
