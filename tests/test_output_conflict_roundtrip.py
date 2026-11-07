import json
import sys

import crawler
from kill_numbers.domain.models import CrawlResult

HEALTHY_NAMES = ("乙站", "丙站", "丁站", "戊站", "己站")


def build_targets():
    targets = [
        {"url": "https://a.test/1", "name": "甲站", "count": 2, "region": "top", "anchor": "栏目"}
    ]
    for index, name in enumerate(HEALTHY_NAMES):
        targets.append(
            {
                "url": f"https://healthy{index}.test/topic",
                "name": name,
                "count": 2,
                "region": "top",
                "anchor": "栏目",
            }
        )
    return targets


def patch_round(monkeypatch, tmp_path, receipt=None):
    targets = build_targets()
    success_file = tmp_path / "310期-杀数字-成功.txt"
    if not success_file.exists():
        success_file.write_text("11,22 甲站\n", encoding="utf-8")
    failed_file = tmp_path / "310期-杀数字-失败.txt"

    def fake_crawl_targets(*_args, **_kwargs):
        results = {0: [CrawlResult(targets[0]["url"], "甲站", "310", ["33", "44"])]}
        for index in range(1, len(targets)):
            results[index] = [
                CrawlResult(targets[index]["url"], targets[index]["name"], "310", ["55", "66"])
            ]
        return results, {}

    monkeypatch.setattr(crawler, "TARGETS", targets)
    monkeypatch.setattr(crawler, "crawl_targets", fake_crawl_targets)
    monkeypatch.setattr(
        crawler,
        "output_files_for_issues",
        lambda issues: (str(success_file), str(failed_file), str(tmp_path / "报告.txt")),
    )
    argv = ["crawler.py", "--issues", "310", "--workers", "1", "--retry-passes", "0"]
    if receipt is not None:
        argv.extend(["--no-cache-update", "--run-result", str(receipt)])
    monkeypatch.setattr(sys, "argv", argv)
    return success_file, failed_file


def test_conflict_site_keeps_round_and_never_reaches_cache(monkeypatch, tmp_path):
    success_file, failed_file = patch_round(monkeypatch, tmp_path)
    captured = {}

    def fake_persist(_path, results, failures, _issues):
        captured["success"] = [item.name for item in results]
        captured["failure"] = [item.name for item in failures]
        return None

    monkeypatch.setattr(crawler, "persist_cache_after_realtime_result", fake_persist)

    assert crawler._main_unlocked() == 1

    # 冲突站不写入，健康站照常写入
    written = success_file.read_text(encoding="utf-8").splitlines()
    assert written[0] == "11,22 甲站"
    assert written[1:] == [f"55,66 {name}" for name in HEALTHY_NAMES]
    failure_text = failed_file.read_text(encoding="utf-8")
    assert "甲站" in failure_text and "[输出冲突]" in failure_text
    # 冲突站不算成功：缓存口径排除它，失败口径包含它
    assert captured["success"] == list(HEALTHY_NAMES)
    assert "甲站" in captured["failure"]


def test_conflict_site_is_excluded_from_run_receipt(monkeypatch, tmp_path):
    receipt = tmp_path / "result.json"
    patch_round(monkeypatch, tmp_path, receipt=receipt)

    assert crawler._main_unlocked() == 1

    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["issue"] == "310"
    assert [row[0] for row in payload["successes"]] == list(HEALTHY_NAMES)
