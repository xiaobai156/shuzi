"""Small per-target transport policy, copied to child-fetch/browser workers."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
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


CURRENT_POLICY = ContextVar('fetch_policy', default=FetchPolicy())


def url_origin(url):
    value = urlsplit(url)
    port = value.port or (443 if value.scheme == 'https' else 80)
    return f'{value.scheme.lower()}://{(value.hostname or "").lower()}:{port}'


@contextmanager
def target_policy(target, issues=()):
    from kill_numbers.text_utils import as_list
    urls = [str(target['url'])]
    if target.get('api_url'):
        urls.append(str(target['api_url']))
    hosts = frozenset(str(h).lower() for h in target.get('allowed_resource_hosts', []))
    insecure = frozenset((urlsplit(u).hostname or '').lower() for u in urls) if target.get('insecure_tls') is True else frozenset()
    policy = FetchPolicy(frozenset(url_origin(u) for u in urls), hosts, insecure,
                        tuple(as_list(target.get('anchor'))), tuple(issues),
                        str(target.get('browser_ready_selector') or ''),
                        str(target.get('source_url_pattern') or ''))
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


def validate_request_url(url, *, resolve=True):
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('采集URL只允许无内嵌凭据的http/https地址')
    hostname = parsed.hostname.rstrip('.').lower()
    if hostname == 'localhost' or hostname.endswith('.localhost'):
        raise ValueError('禁止访问本地/私有网络地址')
    policy = CURRENT_POLICY.get()
    if policy.origins and url_origin(url) not in policy.origins and hostname not in policy.allowed_hosts:
        raise ValueError('跨域来源未获目标配置授权')
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise ValueError('禁止访问本地/私有网络地址')
    if resolve:
        infos = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), type=socket.SOCK_STREAM)
        if not infos or any(not ipaddress.ip_address(info[4][0].split('%')[0]).is_global for info in infos):
            raise ValueError('DNS指向本地/私有网络地址，已拒绝')
    return url


def insecure_for(url):
    return (urlsplit(url).hostname or '').lower() in CURRENT_POLICY.get().insecure_hosts
