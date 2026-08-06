import gzip
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
from urllib.request import Request, urlopen


REQUEST_RETRIES = 5
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
    context = ssl._create_unverified_context()
    try:
        context.set_ciphers("DEFAULT:@SECLEVEL=1")
    except ssl.SSLError:
        pass
    return context


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
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise RuntimeError("curl 不可用")
    cmd = [
        curl,
        "--location",
        "--fail-with-body",
        "--silent",
        "--show-error",
        "--http1.1",
        "--ssl-no-revoke",
        "--connect-timeout",
        str(min(15, timeout)),
        "--max-time",
        str(timeout),
        "--retry",
        "2",
        "--retry-delay",
        "2",
        "--retry-all-errors",
        "-A",
        HEADERS["User-Agent"],
        "-H",
        f"Accept: {HEADERS['Accept']}",
        "-H",
        f"Accept-Language: {HEADERS['Accept-Language']}",
        "-H",
        "Accept-Encoding: identity",
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        if not stderr:
            stderr = proc.stdout.decode("utf-8", errors="replace").strip()[:300]
        raise RuntimeError(f"curl exit {proc.returncode}: {stderr}")
    return proc.stdout


def fetch_bytes(url: str, timeout: int = 25) -> bytes:
    last_error = None
    for attempt in range(REQUEST_RETRIES):
        try:
            wait_for_host_slot(url)
            req = Request(url, headers=HEADERS)
            with urlopen(req, timeout=timeout, context=SSL_CONTEXT) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code == 404:
                raise
            last_error = exc
            if attempt < REQUEST_RETRIES - 1:
                delay = min(8.0, 0.9 * (2**attempt)) + random.uniform(0.2, 0.8)
                time.sleep(delay)
        except Exception as exc:
            last_error = exc
            if attempt < REQUEST_RETRIES - 1:
                delay = min(8.0, 0.9 * (2**attempt)) + random.uniform(0.2, 0.8)
                time.sleep(delay)
    if last_error and is_retryable_network_error(last_error):
        try:
            wait_for_host_slot(url)
            return curl_fetch_bytes(url, timeout=timeout)
        except Exception as curl_exc:
            raise RuntimeError(f"{last_error}; curl 连接失败: {curl_exc}") from curl_exc
    raise last_error


def decode_response(data: bytes) -> str:
    if not data:
        return ""
    if data.startswith(b"\x1f\x8b"):
        data = gzip.decompress(data)
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
