from kill_numbers.application.batch_service import iter_completed_batch, run_ordered_batch
from kill_numbers.application.crawl_service import run_crawl_batch
from kill_numbers.domain.models import CrawlResult


def test_completed_batch_keeps_each_item_and_captures_worker_exception():
    def worker(value: int) -> int:
        if value == 2:
            raise RuntimeError("bad item")
        return value * 10

    entries = list(iter_completed_batch([1, 2, 3], worker, workers=2))
    by_item = {entry.item: entry for entry in entries}

    assert by_item[1].result == 10
    assert by_item[3].result == 30
    assert isinstance(by_item[2].error, RuntimeError)


def test_ordered_batch_returns_input_order_after_concurrent_execution():
    assert run_ordered_batch([3, 1, 2], lambda value: value * 2, workers=3) == [6, 2, 4]


def test_crawl_batch_preserves_target_indexes_and_converts_exception_to_failure():
    events = []
    targets = [
        (8, {"url": "https://one.test", "name": "成功"}),
        (2, {"url": "https://two.test", "name": "异常"}),
    ]

    def runner(target: dict, issues: list[str]):
        if target["name"] == "异常":
            raise RuntimeError("network boom")
        return [CrawlResult(target["url"], target["name"], issues[0], ["01", "02"])], None

    results, failures = run_crawl_batch(
        targets,
        ["211"],
        workers=2,
        runner=runner,
        done_offset=3,
        failure_offset=1,
        total_override=9,
        on_progress=events.append,
    )

    assert results[8][0].numbers == ["01", "02"]
    assert results[2] == []
    assert failures[8] is None
    assert failures[2] is not None
    assert failures[2].reason == "network boom"
    assert {event.done_count for event in events} == {4, 5}
    assert all(event.total == 9 for event in events)
    assert max(event.success_count for event in events) == 1
    assert max(event.failure_count for event in events) == 2
