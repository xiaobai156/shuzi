import argparse
import copy
import re
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import check_duplicates
import crawler
from kill_numbers.application.batch_service import run_ordered_batch
from kill_numbers.infrastructure.file_store import atomic_write_json
from kill_numbers.validation.result_validator import validate_crawl_results


FORMAL_LOAD_TARGETS = crawler.load_targets
VALIDATION_GUARD_LOCK = threading.RLock()

# Only site names explicitly supplied by the user are added here.
FAILED_SITE_NAMES: list[str] = [
    "战胜庄家",
    "金宝神皇",
    "铭记于心",
    "落日余晖",
    "花香鸟语",
    "富甲一方",
    "澳门大佬",
    "师太",
]

# Future fixes are tested here first. They do not change targets.json or crawler.py.
EXPERIMENTAL_TARGET_OVERRIDES: dict[str, dict] = {
    "凤舞九天": {
        "anchor": "（凤舞九天•绝杀10码）",
        "stop_anchor": "（天上地下•绝杀半波）",
        "issue_position_window": 3,
    },
}

EXPERIMENTAL_RUNNERS: dict[str, Callable] = {}

# Compatibility names for old regression tests. All delegate to the formal registry.
extract_buke_article_history_numbers = (
    crawler.extract_top_article_history_current_cycle_numbers
)
extract_admin_identity_bottom_numbers = (
    crawler.extract_identity_article_bottom_10_numbers
)


def extract_shita_consecutive_top_numbers(
    content: str,
    issues: list[str],
    target: dict,
) -> dict[str, list[str]]:
    return crawler.extract_dedicated_ten_numbers(
        content,
        issues,
        target,
        "shita_top_10",
    )




@dataclass
class SiteValidation:
    name: str
    url: str
    requested_issues: list[str]
    results: list[crawler.CrawlResult]
    failure_reason: str = ""

    @property
    def result_by_issue(self) -> dict[str, crawler.CrawlResult]:
        return {
            crawler.normalize_issue(result.issue): result
            for result in self.results
        }

    @property
    def missing_issues(self) -> list[str]:
        found = set(self.result_by_issue)
        return [issue for issue in self.requested_issues if issue not in found]

    @property
    def passed(self) -> bool:
        return not self.failure_reason and not self.missing_issues


def unique_keep_order(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def validation_names(explicit_names: list[str]) -> list[str]:
    """An explicit CLI list is an isolated repair run, never an additive batch."""
    return unique_keep_order(explicit_names or list(FAILED_SITE_NAMES))


def validate_experimental_target(target: dict) -> dict:
    try:
        with tempfile.TemporaryDirectory(prefix="failed-site-validator-") as temp_dir:
            path = Path(temp_dir) / "target.json"
            atomic_write_json(path, [target])
            validated = FORMAL_LOAD_TARGETS(path)
    except Exception as exc:
        name = target.get("name") or target.get("url") or "未命名"
        raise ValueError(f"实验配置无效：{name}；{exc}") from exc
    if len(validated) != 1:
        name = target.get("name") or target.get("url") or "未命名"
        raise ValueError(f"实验配置无效：{name}；目标被禁用或未通过正式配置加载")
    return copy.deepcopy(validated[0])


def select_targets(names: list[str], targets: list[dict]) -> tuple[list[dict], list[str]]:
    by_name = {
        str(target.get("name") or target.get("url") or "").strip(): target
        for target in targets
    }
    selected = []
    missing = []
    for name in unique_keep_order(names):
        target = by_name.get(name)
        if target is None:
            missing.append(name)
            continue
        merged = copy.deepcopy(target)
        merged.update(copy.deepcopy(EXPERIMENTAL_TARGET_OVERRIDES.get(name, {})))
        selected.append(validate_experimental_target(merged))
    return selected, missing


def validate_runner_results(
    target: dict,
    requested_issues: list[str],
    results: list[crawler.CrawlResult],
    failure: crawler.CrawlFailure | None,
) -> tuple[list[crawler.CrawlResult], str]:
    return validate_crawl_results(
        target,
        requested_issues,
        results,
        failure,
    )


@contextmanager
def validation_write_guard():
    with VALIDATION_GUARD_LOCK:
        crawler_names = [
            "save_debug_page",
            "atomic_write_text",
            "output_files_for_issues",
            "backup_existing_outputs",
            "cleanup_old_backups",
            "update_recent_duplicate_cache",
            "write_outputs",
            "remove_stale_failure_file",
            "write_report",
        ]
        duplicate_names = ["write_records_cache", "write_report", "write_report_v2"]
        crawler_originals = {name: getattr(crawler, name) for name in crawler_names}
        duplicate_originals = {
            name: getattr(check_duplicates, name)
            for name in duplicate_names
        }

        def blocked(*_args, **_kwargs):
            raise RuntimeError("独立验证禁止写正式文件")

        for name in crawler_names:
            setattr(crawler, name, blocked)
        crawler.save_debug_page = lambda *_args, **_kwargs: None
        for name in duplicate_names:
            setattr(check_duplicates, name, blocked)
        try:
            yield
        finally:
            for name, value in crawler_originals.items():
                setattr(crawler, name, value)
            for name, value in duplicate_originals.items():
                setattr(check_duplicates, name, value)


def validate_targets(
    targets: list[dict],
    issues: list[str],
    workers: int = 8,
    default_crawl: Callable | None = None,
) -> list[SiteValidation]:
    if workers <= 0:
        raise ValueError("workers 必须大于 0")
    if not targets:
        return []
    normalized_issues = unique_keep_order(
        [crawler.normalize_issue(issue) for issue in issues if crawler.normalize_issue(issue)]
    )
    if not normalized_issues:
        raise ValueError("至少提供一个有效期数")
    crawl = default_crawl or crawler.crawl_one

    def run(target: dict) -> SiteValidation:
        name = str(target.get("name") or target["url"])
        runner = EXPERIMENTAL_RUNNERS.get(name, crawl)
        try:
            results, failure = runner(target, list(normalized_issues))
            accepted, failure_reason = validate_runner_results(
                target,
                normalized_issues,
                list(results),
                failure,
            )
            return SiteValidation(
                name=name,
                url=str(target["url"]),
                requested_issues=list(normalized_issues),
                results=accepted,
                failure_reason=failure_reason,
            )
        except Exception as exc:
            return SiteValidation(
                name=name,
                url=str(target["url"]),
                requested_issues=list(normalized_issues),
                results=[],
                failure_reason=f"验证脚本异常：{exc}",
            )

    with validation_write_guard():
        if workers == 1:
            return [run(target) for target in targets]

        return run_ordered_batch(targets, run, min(workers, len(targets)))


def print_validations(validations: list[SiteValidation]) -> None:
    total = len(validations)
    for index, validation in enumerate(validations, start=1):
        print(f"\n[{index}/{total}] {validation.name}")
        print(f"网址：{validation.url}")
        by_issue = validation.result_by_issue
        for issue in validation.requested_issues:
            result = by_issue.get(issue)
            if result:
                print(f"{issue}期：成功 {' '.join(result.numbers)}")
            else:
                reason = validation.failure_reason or "指定期数没有返回成功数据"
                print(f"{issue}期：失败 {reason}")

    passed = sum(validation.passed for validation in validations)
    print(f"\n验证完成：通过 {passed}/{total}，失败 {total - passed}/{total}")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="只验证人工清单中的站点，不读取失败TXT，不写正式文件或缓存。"
    )
    parser.add_argument("--issues", required=True, help="指定期数，例如 195 或 194,195")
    parser.add_argument("--name", action="append", default=[], help="临时追加验证目录，可重复传入")
    parser.add_argument("--workers", type=int, default=8, help="并发数，默认 8")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        issues = crawler.parse_issues(args.issues)
    except (TypeError, ValueError) as exc:
        print(f"期数格式错误：{exc}")
        return 2
    if not issues:
        print("没有可用期数。")
        return 2
    if args.workers <= 0:
        print("--workers 必须大于 0。")
        return 2

    names = validation_names(list(args.name))
    if not names:
        print("人工验证清单为空；请先把用户明确提供的站点加入 FAILED_SITE_NAMES。")
        return 2

    try:
        selected, missing = select_targets(names, crawler.load_targets())
    except (OSError, ValueError) as exc:
        print(exc)
        return 2
    print(f"只验证失败站点：{len(selected)} 个")
    print("不会写正式成功/失败 TXT，不会更新 recent_10_cache.json。")
    if missing:
        print("targets.json 中未找到：" + "、".join(missing))
    if not selected:
        return 2

    validations = validate_targets(selected, issues, workers=args.workers)
    print_validations(validations)
    return 0 if not missing and all(item.passed for item in validations) else 1


if __name__ == "__main__":
    raise SystemExit(main())
