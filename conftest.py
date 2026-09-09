"""Tests run offline and never write the production output/cache directories."""
import socket
import pytest
import crawler


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(crawler, 'RESULTS_DIR', tmp_path / 'results')
    monkeypatch.setattr(crawler, 'DEBUG_DIR', tmp_path / 'debug')
    monkeypatch.setattr(crawler, 'CACHE_FILE', tmp_path / 'recent_10_cache.json')
    monkeypatch.setattr(crawler, 'CACHE_STATE_FILE', tmp_path / '.cache-state.json')
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('SHUZI_CYCLE_ID', raising=False)

    def offline(*args, **kwargs):
        raise AssertionError('Unit tests must use fake transports; live network is disabled')

    monkeypatch.setattr(socket, 'create_connection', offline)
