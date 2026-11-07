import sys

import crawler


def test_crawler_rejects_multiple_issues_before_touching_outputs(monkeypatch, tmp_path):
    """多期混写同一文件会让行无法区分期数，入口必须直接拒绝。"""
    result_file = tmp_path / "309-310期-杀数字-成功.txt"
    monkeypatch.setattr(crawler, "RESULTS_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["crawler.py", "--issues", "309,310"])

    assert crawler.main() == 2
    assert not result_file.exists()
    assert not any(tmp_path.iterdir())
