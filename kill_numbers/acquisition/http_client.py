import gzip
import io
import json
import random
import re
import shutil
import ssl
import subprocess
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler
from kill_numbers.acquisition.policy import validate_request_url, insecure_for

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_request_url(newurl)
        if req.full_url.startswith("https://") and not newurl.startswith("https://"):
            raise ValueError("拒绝 HTTPS 降级重定向")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def urlopen(request, *, timeout, context):
    opener = build_opener(HTTPSHandler(context=context), SafeRedirectHandler())
    return opener.open(request, timeout=timeout)



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
