import check_duplicates
import crawler
import inspect
import validate_failed_sites as validator
from kill_numbers.domain.models import CrawlResult


def test_crawler_batch_uses_the_application_single_target_boundary(monkeypatch):
    target = {"url": "https://example.test/one", "name": "测试"}
    calls = []

    def fake_formal_core(_dependencies, received_target: dict, issues: list[str]):
        calls.append((received_target, issues))
        return [CrawlResult(target["url"], target["name"], issues[0], ["01", "02"])], None

    monkeypatch.setattr(crawler, "run_formal_crawl_target", fake_formal_core)

    results, failures = crawler.crawl_targets([(7, target)], ["211"], max_workers=1)

    assert calls == [(target, ["211"])]
    assert results[7][0].issue == "211"
    assert failures[7] is None


def test_duplicate_recheck_uses_shared_batch_without_network(monkeypatch):
    targets = [
        {"url": "https://one.test", "name": "一"},
        {"url": "https://two.test", "name": "二"},
    ]
    monkeypatch.setattr(crawler, "TARGETS", targets)

    def fake_crawl(target: dict, issues: list[str]):
        if target["name"] == "二":
            raise RuntimeError("network failure")
        return [CrawlResult(target["url"], target["name"], issues[0], ["01", "02"])], None

    monkeypatch.setattr(crawler, "crawl_one", fake_crawl)

    records, problems = check_duplicates.records_from_crawler(["211"], workers=2)

    assert [(record.name, record.issue, record.numbers) for record in records] == [
        ("一", "211期", "01,02"),
    ]
    assert [(problem.name, problem.reason) for problem in problems] == [("二", "network failure")]


def test_formal_entry_does_not_run_or_write_manual_risk_precheck():
    source = inspect.getsource(crawler._main_unlocked)

    assert "resolve_manual_risk_targets" not in source
    assert "merge_active_targets_into_file" not in source


def test_failed_site_parallel_validation_uses_shared_ordered_batch(monkeypatch):
    targets = [
        {"url": "https://one.test", "name": "一"},
        {"url": "https://two.test", "name": "二"},
    ]
    calls = {}

    def fake_ordered_batch(items, worker, workers):
        values = list(items)
        calls["workers"] = workers
        calls["names"] = [target["name"] for target in values]
        return [worker(target) for target in values]

    def fake_validate(_target, _issues, results, _failure):
        return results, ""

    def fake_crawl(target: dict, issues: list[str]):
        return [CrawlResult(target["url"], target["name"], issues[0], ["01", "02"])], None

    monkeypatch.setattr(validator, "run_ordered_batch", fake_ordered_batch)
    monkeypatch.setattr(validator, "validate_runner_results", fake_validate)

    validations = validator.validate_targets(targets, ["211"], workers=2, default_crawl=fake_crawl)

    assert calls == {"workers": 2, "names": ["一", "二"]}
    assert [validation.name for validation in validations] == ["一", "二"]


def test_duplicate_bat_uses_per_site_latest_periods():
    content = (
        crawler.SCRIPT_DIR / "爬虫-每天杀数字 - 检测重复.bat"
    ).read_text(encoding="utf-8")

    assert "--latest" not in content
    assert "check_duplicates.py --recent 10" in content
    assert "--write-cache" in content
