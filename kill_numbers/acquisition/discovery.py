import base64
import html
import re
from urllib.parse import urljoin, urlparse

from kill_numbers.text_utils import (
    unique_keep_order,
)


SCRIPT_WORKERS = 2


def decode_strdecode_payloads(value: str) -> list[str]:
    decoded = []
    patterns = [
        r"strdecode\([\"']([^\"']+)[\"']\)",
        r"decodeB64\([\"']([^\"']+)[\"']\)",
        r"__PAGE_DATA__\s*=\s*[\"']([^\"']+)[\"']",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, value):
            payload = match.group(1)
            try:
                decoded.append(base64.b64decode(payload).decode("utf-8", errors="ignore"))
            except Exception:
                continue
    return decoded


def is_fetchable_script(src: str, page_url: str) -> bool:
    parsed_src = urlparse(src)
    parsed_page = urlparse(page_url)
    if "hm.baidu.com" in parsed_src.netloc:
        return False
    if "/upload/script/" in src:
        return True
    if parsed_src.netloc == parsed_page.netloc:
        return True
    if not parsed_src.netloc:
        return True
    return False


def script_urls(html_value: str, page_url: str) -> list[str]:
    urls = []
    for match in re.finditer(r"<script[^>]+src=[\"']([^\"']+)[\"']", html_value, re.I):
        src = html.unescape(match.group(1))
        absolute = urljoin(page_url, src)
        if is_fetchable_script(absolute, page_url):
            urls.append(absolute)
    return unique_keep_order(urls)


def iframe_urls(html_value: str, page_url: str) -> list[str]:
    urls = []
    for match in re.finditer(r"<iframe[^>]+src=[\"']([^\"']+)[\"']", html_value, re.I):
        src = html.unescape(match.group(1)).strip()
        if src and not src.lower().startswith(("javascript:", "data:")):
            urls.append(urljoin(page_url, src))
    return unique_keep_order(urls)
