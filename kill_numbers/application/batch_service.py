from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Generic, TypeVar, cast


Item = TypeVar("Item")
Result = TypeVar("Result")


@dataclass(frozen=True)
class BatchCompletion(Generic[Item, Result]):
    index: int
    item: Item
    result: Result | None
    error: BaseException | None


def iter_completed_batch(
    items: Iterable[Item],
    worker: Callable[[Item], Result],
    workers: int,
) -> Iterator[BatchCompletion[Item, Result]]:
    """Yield completed work without giving callers ownership of a thread pool."""
    values = list(items)
    if workers <= 0:
        raise ValueError("workers 必须大于 0")
    if not values:
        return

    with ThreadPoolExecutor(max_workers=min(workers, len(values))) as pool:
        futures = {
            pool.submit(worker, item): (index, item)
            for index, item in enumerate(values)
        }
        for future in as_completed(futures):
            index, item = futures[future]
            try:
                yield BatchCompletion(index=index, item=item, result=future.result(), error=None)
            except BaseException as exc:
                yield BatchCompletion(index=index, item=item, result=None, error=exc)


def run_ordered_batch(
    items: Iterable[Item],
    worker: Callable[[Item], Result],
    workers: int,
) -> list[Result]:
    """Run a batch concurrently while returning values in input order."""
    values = list(items)
    missing = object()
    completed: list[Result | object] = [missing] * len(values)
    for entry in iter_completed_batch(values, worker, workers):
        if entry.error is not None:
            raise entry.error
        completed[entry.index] = entry.result
    if any(value is missing for value in completed):
        raise RuntimeError("批量任务未返回完整结果")
    return [cast(Result, value) for value in completed]
