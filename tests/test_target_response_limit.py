import gzip
import io

import pytest

import crawler
from kill_numbers.acquisition import http_client, policy


class _Response(io.BytesIO):
    def __init__(self, data: bytes, url='https://a.test/data', content_type='text/plain'):
        super().__init__(data)
        self._url = url
        self.headers = {'Content-Type': content_type}

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _public_dns(monkeypatch):
    monkeypatch.setattr(
        policy.socket,
        'getaddrinfo',
        lambda *a, **k: [(policy.socket.AF_INET, policy.socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))],
    )


def test_target_scoped_response_limit_applies_only_inside_target(monkeypatch):
    _public_dns(monkeypatch)
    monkeypatch.setattr(http_client, 'MAX_RESPONSE_BYTES', 16)
    monkeypatch.setattr(http_client, 'wait_for_host_slot', lambda url: None)
    monkeypatch.setattr(
        http_client,
        'urlopen',
        lambda *a, **k: _Response(b'a' * 17),
    )

    with pytest.raises(ValueError, match='大小限制'):
        http_client.fetch_bytes('https://a.test/data')

    with policy.target_policy({'url': 'https://a.test', 'max_response_bytes': 32}):
        assert http_client.fetch_bytes('https://a.test/data') == b'a' * 17
        assert http_client.decode_response(gzip.compress(b'a' * 17)) == 'a' * 17

    with pytest.raises(ValueError, match='大小限制'):
        http_client.fetch_bytes('https://a.test/data')


@pytest.mark.parametrize('value', [True, 0, -1, 16 * 1024 * 1024 + 1, '10485760'])
def test_invalid_target_response_limit_fails_before_fetch(value):
    with pytest.raises(ValueError, match='max_response_bytes'):
        with policy.target_policy({'url': 'https://a.test', 'max_response_bytes': value}):
            pass


def test_content_script_keeps_default_limit_even_when_target_allows_more(monkeypatch):
    _public_dns(monkeypatch)
    monkeypatch.setattr(http_client, 'MAX_RESPONSE_BYTES', 16)
    monkeypatch.setattr(http_client, 'wait_for_host_slot', lambda url: None)
    script_url = 'https://a.test/upload/script/data.js'
    monkeypatch.setattr(
        http_client,
        'urlopen',
        lambda *a, **k: _Response(b'x' * 17, url=script_url, content_type='application/javascript'),
    )
    with policy.target_policy({'url': 'https://a.test', 'max_response_bytes': 32}):
        with pytest.raises(ValueError, match='大小限制'):
            http_client._content_script_once(
                script_url,
                'https://a.test/topic/1',
                25,
                http_client.SSL_CONTEXT,
            )


def test_only_verified_large_landing_targets_receive_10mib_contract():
    import json
    from pathlib import Path

    targets = json.loads(crawler.TARGETS_FILE.read_text(encoding='utf-8'))
    configured = {
        target['name']: target['max_response_bytes']
        for target in targets
        if 'max_response_bytes' in target
    }
    assert configured == {
        '蔡邕救琴': 10 * 1024 * 1024,
        '茂名神码': 10 * 1024 * 1024,
    }
