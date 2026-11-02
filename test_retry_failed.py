from pathlib import Path
import retry_failed

def test_atomic_bytes_cleans_and_preserves_crlf(tmp_path):
    p = tmp_path / "x.txt"
    retry_failed._write_bytes(p, b"a\r\nb\r\n")
    assert p.read_bytes() == b"a\r\nb\r\n"

def test_filename_issue_guard(tmp_path):
    p = tmp_path / "1期-杀数字-失败.txt"
    p.write_bytes(b"\xef\xbb\xbf")
    assert retry_failed.retry_failed_file(p, "2")[2] == 2


def _scoped_retry_fixture(monkeypatch, tmp_path):
    """一个只含单站的失败 TXT + 桩抓取，用于观察缓存写入范围。"""
    import crawler

    monkeypatch.setattr(crawler, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(crawler, "load_targets", lambda: [{"name": "甲", "url": "https://a.test/x", "region": "bottom"}])
    result = crawler.CrawlResult(name="甲", url="https://a.test/x", issue="268", numbers=["01", "02"])
    monkeypatch.setattr(crawler, "crawl_one", lambda target, issues: ([result], None))
    monkeypatch.setattr(retry_failed, "validate_crawl_results", lambda *args: ([result], ""))
    failure_file = tmp_path / "268期-杀数字-失败.txt"
    failure_file.write_text("甲 bottom https://a.test/x 原因：[网络临时失败] x\n", encoding="utf-8")
    return result, failure_file


def test_retry_failed_file_updates_cache_only_for_retried_sites(monkeypatch, tmp_path):
    import crawler

    result, failure_file = _scoped_retry_fixture(monkeypatch, tmp_path)
    seen = []
    monkeypatch.setattr(
        crawler,
        "persist_cache_after_realtime_result",
        lambda path, results, failures, issues: seen.append((list(results), list(failures), list(issues))),
    )

    jobs, successes, status = retry_failed.retry_failed_file(failure_file, "268", update_cache=True)

    assert (jobs, successes, status) == (1, 1, 0)
    assert not failure_file.exists()  # 失败块全部清除后不留空文件
    assert (tmp_path / "268期-杀数字-成功.txt").read_text(encoding="utf-8").strip() == "01,02 甲"
    assert seen == [([result], [], ["268"])]


def test_retry_failed_file_leaves_cache_untouched_by_default(monkeypatch, tmp_path):
    import crawler

    _result, failure_file = _scoped_retry_fixture(monkeypatch, tmp_path)

    def fail(*_args):
        raise AssertionError("默认入口不得写入缓存")

    monkeypatch.setattr(crawler, "persist_cache_after_realtime_result", fail)

    assert retry_failed.retry_failed_file(failure_file, "268")[:2] == (1, 1)


def test_retry_failed_file_keeps_unresolved_failure_blocks(monkeypatch, tmp_path):
    import crawler

    monkeypatch.setattr(crawler, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(
        crawler,
        "load_targets",
        lambda: [
            {"name": "甲", "url": "https://a.test/x", "region": "bottom"},
            {"name": "乙", "url": "https://b.test/y", "region": "bottom"},
        ],
    )
    ok = crawler.CrawlResult(name="甲", url="https://a.test/x", issue="268", numbers=["01", "02"])
    monkeypatch.setattr(
        crawler,
        "crawl_one",
        lambda target, issues: ([ok], None) if target["name"] == "甲" else ([], None),
    )
    monkeypatch.setattr(
        retry_failed,
        "validate_crawl_results",
        lambda target, issues, results, failure: (results, "" if target["name"] == "甲" else "没有找到符合配置的 268期 号码"),
    )
    failure_file = tmp_path / "268期-杀数字-失败.txt"
    failure_file.write_text(
        "甲 bottom https://a.test/x 原因：[网络临时失败] x\n乙 bottom https://b.test/y 原因：[页面无当期] y\n",
        encoding="utf-8",
    )

    jobs, successes, status = retry_failed.retry_failed_file(failure_file, "268")

    assert (jobs, successes, status) == (2, 1, 1)
    kept = failure_file.read_text(encoding="utf-8")
    assert "乙" in kept and "甲" not in kept


def test_retry_failed_file_stops_when_success_txt_was_emptied(monkeypatch, tmp_path):
    """成功 TXT 被清空成 0 字节时必须停写，不得覆盖成只剩本次重抓的行。"""
    result, failure_file = _scoped_retry_fixture(monkeypatch, tmp_path)
    success_file = tmp_path / "268期-杀数字-成功.txt"
    success_file.write_bytes(b"")

    jobs, successes, status = retry_failed.retry_failed_file(failure_file, "268")

    assert status == 1
    assert successes == 0
    assert success_file.read_bytes() == b""          # 未被写入
    assert failure_file.exists()                      # 失败块保留
