"""Small per-target transport policy, copied to child-fetch/browser workers."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
import ipaddress
import socket
from urllib.parse import urlsplit


@dataclass(frozen=True)
class FetchPolicy:
    origins: frozenset[str] = frozenset()
    allowed_hosts: frozenset[str] = frozenset()
    insecure_hosts: frozenset[str] = frozenset()
    anchors: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()
    ready_selector: str = ''
    source_url_pattern: str = ''
    keywords: tuple[str, ...] = ()
    expected_count: int | None = None
    history_discovery: bool = False
    history_depth: int = 0
    max_response_bytes: int | None = None


CURRENT_POLICY = ContextVar('fetch_policy', default=FetchPolicy())


def url_origin(url):
    value = urlsplit(url)
    port = value.port or (443 if value.scheme == 'https' else 80)
    return f'{value.scheme.lower()}://{(value.hostname or "").lower()}:{port}'


MAX_TARGET_RESPONSE_BYTES = 16 * 1024 * 1024


def configured_response_limit(value):
    if value is None:
        return None
    if type(value) is not int or not (1 <= value <= MAX_TARGET_RESPONSE_BYTES):
        raise ValueError('max_response_bytes 必须是 1 到 16MiB 之间的整数')
    return value


@contextmanager
def target_policy(target, issues=()):
    from kill_numbers.text_utils import as_list
    urls = [str(target['url'])]
    if target.get('api_url'):
        urls.append(str(target['api_url']))
    hosts = frozenset(str(h).lower() for h in target.get('allowed_resource_hosts', []))
    insecure = frozenset((urlsplit(u).hostname or '').lower() for u in urls) if target.get('insecure_tls') is True else frozenset()
    count = target.get('count') if type(target.get('count')) is int else None
    depth = target.get('_history_depth') if type(target.get('_history_depth')) is int else 0
    max_response_bytes = configured_response_limit(target.get('max_response_bytes'))
    policy = FetchPolicy(
        origins=frozenset(url_origin(u) for u in urls),
        allowed_hosts=hosts,
        insecure_hosts=insecure,
        anchors=tuple(as_list(target.get('anchor'))),
        issues=tuple(str(issue) for issue in issues),
        ready_selector=str(target.get('browser_ready_selector') or ''),
        source_url_pattern=str(target.get('source_url_pattern') or ''),
        keywords=tuple(str(value) for value in as_list(target.get('keywords')) if str(value).strip()),
        expected_count=count,
        history_discovery=target.get('_history_discovery') is True,
        history_depth=depth,
        max_response_bytes=max_response_bytes,
    )
    token = CURRENT_POLICY.set(policy)
    try:
        yield policy
    finally:
        CURRENT_POLICY.reset(token)


def child_url_allowed(url, parent_url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            return False
        return (url_origin(url) == url_origin(parent_url)
                or parsed.hostname.lower() in CURRENT_POLICY.get().allowed_hosts)
    except ValueError:
        return False


def validate_public_request_url(url, *, resolve=True):
    """Validate transport safety without granting target-origin authority."""
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('采集URL只允许无内嵌凭据的http/https地址')
    hostname = parsed.hostname.rstrip('.').lower()
    if hostname == 'localhost' or hostname.endswith('.localhost'):
        raise ValueError('禁止访问本地/私有网络地址')
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise ValueError('禁止访问本地/私有网络地址')
    if resolve:
        infos = socket.getaddrinfo(
            hostname,
            parsed.port or (443 if parsed.scheme == 'https' else 80),
            type=socket.SOCK_STREAM,
        )
        if not infos or any(
            not ipaddress.ip_address(info[4][0].split('%')[0]).is_global
            for info in infos
        ):
            raise ValueError('DNS指向本地/私有网络地址，已拒绝')
    return url


def validate_request_url(url, *, resolve=True):
    validate_public_request_url(url, resolve=resolve)
    parsed = urlsplit(url)
    hostname = parsed.hostname.rstrip('.').lower()
    policy = CURRENT_POLICY.get()
    if policy.origins and url_origin(url) not in policy.origins and hostname not in policy.allowed_hosts:
        raise ValueError('跨域来源未获目标配置授权')
    return url


@contextmanager
def allow_discovered_child_url(url):
    """Temporarily authorize one already-discovered public child hostname.

    This does not persist in the target configuration and is intentionally used
    only for direct content-script URLs that were present in the root HTML.
    """
    validate_public_request_url(url)
    hostname = (urlsplit(url).hostname or '').rstrip('.').lower()
    current = CURRENT_POLICY.get()
    token = CURRENT_POLICY.set(
        replace(current, allowed_hosts=current.allowed_hosts | frozenset({hostname}))
    )
    try:
        yield
    finally:
        CURRENT_POLICY.reset(token)


def insecure_for(url):
    return (urlsplit(url).hostname or '').lower() in CURRENT_POLICY.get().insecure_hosts
