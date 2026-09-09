import ast
import base64
import html
import json
import re
from urllib.parse import urljoin, urlparse

from kill_numbers.acquisition.policy import (
    child_url_allowed,
    validate_public_request_url,
)
from kill_numbers.text_utils import unique_keep_order


SCRIPT_WORKERS = 2


def decode_strdecode_payloads(value: str) -> list[str]:
    decoded = []
    patterns = [
        r"strdecode\([\"']([^\"']+)[\"']\)",
        r"decodeB64\([\"']([^\"']+)[\"']\)",
        r"__PAGE_DATA__\s*=\s*[\"']([^\"']+)[\"']",
    ]
    matches = sorted(
        (match for pattern in patterns for match in re.finditer(pattern, value)),
        key=lambda match: match.start(),
    )
    for match in matches:
        try:
            decoded.append(base64.b64decode(match.group(1), validate=True).decode("utf-8"))
        except (ValueError, UnicodeError):
            continue
    return decoded


def is_content_script_url(src: str) -> bool:
    parsed = urlparse(src)
    path = parsed.path.lower()
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and "/upload/script/" in path
        and path.endswith(".js")
    )


def is_fetchable_script(src: str, page_url: str) -> bool:
    if (urlparse(src).hostname or "").lower() == "hm.baidu.com":
        return False
    if child_url_allowed(src, page_url):
        return True
    if not is_content_script_url(src):
        return False
    try:
        # DNS is rechecked immediately before transport. Discovery only proves
        # scheme/credential/literal-address safety so this remains deterministic.
        validate_public_request_url(src, resolve=False)
        return True
    except ValueError:
        return False


def _static_write_expression(expression: str) -> str | None:
    expression = expression.strip()
    encoded = re.fullmatch(
        r"(?:utf8to16\s*\(\s*)?(?:strdecode|decodeB64)\s*\(\s*([\"'])([A-Za-z0-9+/=]+)\1\s*\)\s*\)?",
        expression,
        re.I,
    )
    if encoded:
        try:
            return base64.b64decode(encoded.group(2), validate=True).decode("utf-8")
        except (ValueError, UnicodeError):
            return None
    if (
        len(expression) >= 2
        and expression[0] == expression[-1]
        and expression[0] in {'"', "'"}
    ):
        try:
            value = json.loads(expression) if expression[0] == '"' else ast.literal_eval(expression)
        except (ValueError, SyntaxError, json.JSONDecodeError):
            return None
        return value if isinstance(value, str) else None
    return None


def _write_call_expression(value: str, expression_start: int) -> tuple[str, int] | None:
    depth = 1
    quote = ""
    escaped = False
    index = expression_start
    while index < len(value):
        char = value[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        else:
            if char in {'"', "'"}:
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return value[expression_start:index], index + 1
        index += 1
    return None


def render_document_write_stream(script: str) -> str | None:
    """Replay statically provable document.write/writeln calls in source order.

    Independent decoded fragments are never concatenated. A stream exists only
    when every document.write call is a literal or a supported base64 decoder
    expression, mirroring the browser's actual write order.
    """
    call_pattern = re.compile(r"document\.(write|writeln)\s*\(", re.I)
    outputs: list[str] = []
    search_at = 0
    seen = 0
    while True:
        match = call_pattern.search(script, search_at)
        if match is None:
            break
        seen += 1
        parsed = _write_call_expression(script, match.end())
        if parsed is None:
            return None
        expression, call_end = parsed
        decoded = _static_write_expression(expression)
        if decoded is None:
            return None
        outputs.append(decoded + ("\n" if match.group(1).lower() == "writeln" else ""))
        search_at = call_end
    return "".join(outputs) if seen else None


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
            absolute = urljoin(page_url, src)
            if child_url_allowed(absolute, page_url):
                urls.append(absolute)
    return unique_keep_order(urls)
