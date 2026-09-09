import time
from kill_numbers.acquisition.policy import target_policy
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from kill_numbers.acquisition.browser_pool import render_page_documents
from kill_numbers.acquisition.documents import document_debug_text
from kill_numbers.application.batch_service import iter_completed_batch
from kill_numbers.domain.models import CrawlFailure, CrawlResult, SourceDocument
from kill_numbers.parsing.common import CANDIDATE_REGION_WINDOW, manual_risk_reason
from kill_numbers.parsing.diagnostics import (
    detect_available_issues,
    diagnose_issue_mismatch,
    nearest_issues,
)
from kill_numbers.parsing.dedicated.site_parsers import (
    fengwu_jiutian_bottom_10_available_issues,
    huxin_xiaozhu_stable_10_available_issues,
    identity_article_bottom_10_available_issues,
    qiancai_liangde_bottom_10_available_issues,
    xinzhu_forum_top_candidates,
)
from kill_numbers.parsing.registry import parse_target_content
from kill_numbers.text_utils import clean_name
from kill_numbers.validation.result_validator import (
    evidence_from_source_document,
    validate_issue_map,
)


CrawlRunner = Callable[[dict, list[str]], tuple[list[CrawlResult], CrawlFailure | None]]
DocumentFetcher = Callable[[dict, list[str]], tuple[str, list[SourceDocument]]]
DocumentParser = Callable[
    [list[SourceDocument], dict, list[str]],
    tuple[dict[str, list[str]], SourceDocument | None],
]
LinkedPageCrawler = Callable[
    [dict, list[str]],
    tuple[str, str, dict[str, list[str]], dict[str, SourceDocument]],
]
SplitContractChecker = Callable[[list[SourceDocument], dict], bool]
DebugWriter = Callable[[dict, list[str], str, str, str], Path | None]


@dataclass(frozen=True)
class CrawlDependencies:
    """Site acquisition adapters supplied by the compatibility entry module."""

    fetch_target_documents: DocumentFetcher
    parse_target_documents: DocumentParser
    crawl_zuojianzifu_link_chain: LinkedPageCrawler
    contract_is_split_across_documents: SplitContractChecker
    save_debug_page: DebugWriter
    crawl_ttss_paginated_identity_top_10: LinkedPageCrawler | None = None


@dataclass(frozen=True)
class CrawlBatchProgress:
    completed_count: int
    done_count: int
    total: int
    success_count: int
    failure_count: int
    elapsed_seconds: float
    index: int
    target: dict
    results: list[CrawlResult]
    failure: CrawlFailure | None


def run_crawl_target(
    runner: CrawlRunner,
    target: dict,
    issues: list[str],
) -> tuple[list[CrawlResult], CrawlFailure | None]:
    """The one-target boundary used by every batch orchestration path."""
    return runner(target, list(issues))


def run_formal_crawl_target(dependencies, target, issues):
    with target_policy(target, issues):
        return _run_formal_crawl_target(dependencies, target, issues)


def _run_formal_crawl_target(
    dependencies: CrawlDependencies,
    target: dict,
    issues: list[str],
) -> tuple[list[CrawlResult], CrawlFailure | None]:
    """Run the sole formal per-site pipeline without letting entrypoints parse data."""
    url = str(target["url"])
    configured_name = str(target.get("name") or "").strip() or "未命名"
    content = ""
    name = configured_name
    issue_map: dict[str, list[str]] | None = None
    source_documents: list[SourceDocument] | None = None
    selected_document: SourceDocument | None = None
    selected_documents_by_issue: dict[str, SourceDocument] = {}

    if target.get("disabled"):
        return [], CrawlFailure(url=url, name=configured_name, reason="目标已停用，禁止抓取")
    risk = manual_risk_reason(target)
    if risk:
        return [], CrawlFailure(
            url=url,
            name=configured_name or urlparse(url).netloc,
            reason=f"手动风险拦截：{risk}",
        )

    try:
        if target.get("special_parser") == "zuojianzifu_link_chain":
            (
                auto_name,
                content,
                issue_map,
                selected_documents_by_issue,
            ) = dependencies.crawl_zuojianzifu_link_chain(target, issues)
        elif target.get("special_parser") == "ttss_paginated_identity_top_10":
            if dependencies.crawl_ttss_paginated_identity_top_10 is None:
                raise ValueError("分页身份文章专属采集器未配置")
            (
                auto_name,
                content,
                issue_map,
                selected_documents_by_issue,
            ) = dependencies.crawl_ttss_paginated_identity_top_10(target, issues)
        else:
            auto_name, source_documents = dependencies.fetch_target_documents(target, issues)

        name = configured_name if configured_name != "未命名" else auto_name
        if source_documents is not None:
            issue_map, selected_document = dependencies.parse_target_documents(
                source_documents,
                target,
                issues,
            )
            requested_issue_set = {
                str(issue).strip()
                for issue in issues
                if str(issue).strip()
            }
            parsed_issue_set = set(issue_map or {})
            if not requested_issue_set.issubset(parsed_issue_set) and (
                target.get("browser_fallback") is True
                or dependencies.contract_is_split_across_documents(source_documents, target)
            ):
                try:
                    browser_documents = render_page_documents(url)
                except Exception:
                    browser_documents = []
                if browser_documents:
                    source_documents.extend(browser_documents)
                    issue_map, selected_document = dependencies.parse_target_documents(
                        source_documents,
                        target,
                        issues,
                    )
            content = (
                selected_document.content
                if selected_document is not None
                else document_debug_text(source_documents)
            )
        elif issue_map is None:
            issue_map = parse_target_content(content, target, issues)
        if not issue_map:
            if target.get("special_parser") == "huxin_xiaozhu_stable_10":
                available = huxin_xiaozhu_stable_10_available_issues(content, target)
                wanted = ",".join(f"{issue}期" for issue in issues)
                found_text = ",".join(f"{issue}期" for issue in available)
                reason = (
                    f"湖心小筑绝杀十码专属栏目顶部前 "
                    f"{CANDIDATE_REGION_WINDOW} 条"
                    f"没有指定期数 {wanted}；同栏目找到：{found_text or '无'}"
                )
                debug_path = dependencies.save_debug_page(target, issues, name, content, reason)
                if debug_path:
                    reason = f"{reason}；调试页面：{debug_path.name}"
                return [], CrawlFailure(url=url, name=name, reason=reason)
            if target.get("special_parser") == "xinzhu_forum_stable_10":
                available = list(xinzhu_forum_top_candidates(content, target))
                wanted = ",".join(f"{issue}期" for issue in issues)
                found_text = ",".join(f"{issue}期" for issue in available)
                window = target.get("issue_position_window") or CANDIDATE_REGION_WINDOW
                reason = (
                    f"新竹论坛专属栏目顶部前 {window} 条"
                    f"没有指定期数 {wanted}；同栏目找到：{found_text or '无'}"
                )
                debug_path = dependencies.save_debug_page(target, issues, name, content, reason)
                if debug_path:
                    reason = f"{reason}；调试页面：{debug_path.name}"
                return [], CrawlFailure(url=url, name=name, reason=reason)
            if target.get("special_parser") == "fengwu_jiutian_bottom_10":
                available = fengwu_jiutian_bottom_10_available_issues(content, target)
                wanted = ",".join(f"{issue}期" for issue in issues)
                found_text = ",".join(f"{issue}期" for issue in available)
                reason = (
                    "凤舞九天专属栏目尾部最近3条"
                    f"没有指定期数 {wanted}；同栏目找到：{found_text or '无'}"
                )
                debug_path = dependencies.save_debug_page(target, issues, name, content, reason)
                if debug_path:
                    reason = f"{reason}；调试页面：{debug_path.name}"
                return [], CrawlFailure(url=url, name=name, reason=reason)
            if target.get("special_parser") == "qiancai_liangde_bottom_10":
                available = qiancai_liangde_bottom_10_available_issues(content, target)
                wanted = ",".join(f"{issue}期" for issue in issues)
                found_text = ",".join(f"{issue}期" for issue in available)
                reason = (
                    f"钱彩两得专属栏目尾部最近{CANDIDATE_REGION_WINDOW}条"
                    f"没有指定期数 {wanted}；同栏目找到：{found_text or '无'}"
                )
                debug_path = dependencies.save_debug_page(target, issues, name, content, reason)
                if debug_path:
                    reason = f"{reason}；调试页面：{debug_path.name}"
                return [], CrawlFailure(url=url, name=name, reason=reason)
            if target.get("special_parser") == "identity_article_bottom_10":
                available = identity_article_bottom_10_available_issues(content, target)
                wanted = ",".join(f"{issue}期" for issue in issues)
                found_text = ",".join(f"{issue}期" for issue in available)
                window = target.get("issue_position_window") or CANDIDATE_REGION_WINDOW
                reason = (
                    f"{target.get('article_identity')}专属文章尾部最近{window}条"
                    f"没有指定期数 {wanted}；同栏目找到：{found_text or '无'}"
                )
                debug_path = dependencies.save_debug_page(target, issues, name, content, reason)
                if debug_path:
                    reason = f"{reason}；调试页面：{debug_path.name}"
                return [], CrawlFailure(url=url, name=name, reason=reason)
            mismatch = diagnose_issue_mismatch(
                content,
                issues,
                keywords=target.get("keywords"),
                expected_count=target.get("count"),
                allow_duplicate_numbers=target.get("allow_duplicate_numbers", False),
                region=target.get("region"),
                anchor=target.get("anchor"),
                stop_anchor=target.get("stop_anchor"),
                issue_position_window=target.get("issue_position_window"),
            )
            available = detect_available_issues(
                content,
                keywords=target.get("keywords"),
                expected_count=target.get("count"),
                position=target.get("position", "first"),
                allow_duplicate_numbers=target.get("allow_duplicate_numbers", False),
                anchor=target.get("anchor"),
                stop_anchor=target.get("stop_anchor"),
                region=target.get("region"),
                issue_position_window=target.get("issue_position_window"),
            )
            wanted = ",".join(f"{issue}期" for issue in issues)
            if mismatch:
                detail = "；".join(
                    f"{issue}期：{message}" for issue, message in mismatch.items()
                )
                reason = f"没有找到符合配置的 {wanted} 号码；{detail}"
            elif available:
                found_text = ",".join(
                    f"{issue}期" for issue in nearest_issues(available, issues)
                )
                reason = f"没有找到指定期数 {wanted} 的号码；本页同栏目找到：{found_text}"
            else:
                reason = f"没有找到指定期数 {wanted} 的号码；本页同栏目没有识别到可用期数"
            debug_path = dependencies.save_debug_page(target, issues, name, content, reason)
            if debug_path:
                reason = f"{reason}；调试页面：{debug_path.name}"
            return [], CrawlFailure(url=url, name=name, reason=reason)

        issue_map = validate_issue_map(
            target,
            issues,
            issue_map,
            source_document=selected_document,
            source_documents=selected_documents_by_issue,
            require_source_document=True,
        )
        results = [
            CrawlResult(
                url=url,
                name=name,
                issue=issue,
                numbers=numbers,
                evidence=evidence_from_source_document(
                    target,
                    issue,
                    numbers,
                    selected_documents_by_issue.get(issue, selected_document),
                ),
            )
            for issue, numbers in issue_map.items()
        ]
        return results, None

    except Exception as exc:
        name = configured_name if configured_name != "未命名" else urlparse(url).netloc
        reason = str(exc)
        debug_path = dependencies.save_debug_page(target, issues, name, content, reason)
        if debug_path:
            reason = f"{reason}；调试页面：{debug_path.name}"
        return [], CrawlFailure(url=url, name=name, reason=reason)


def run_crawl_batch(
    target_items: list[tuple[int, dict]],
    issues: list[str],
    workers: int,
    runner: CrawlRunner,
    *,
    done_offset: int = 0,
    failure_offset: int = 0,
    total_override: int | None = None,
    on_progress: Callable[[CrawlBatchProgress], None] | None = None,
) -> tuple[dict[int, list[CrawlResult]], dict[int, CrawlFailure | None]]:
    """Run crawler work with a stable exception and progress boundary."""
    results_by_index: dict[int, list[CrawlResult]] = {}
    failures_by_index: dict[int, CrawlFailure | None] = {}
    total = total_override if total_override is not None else len(target_items)
    if not target_items:
        return results_by_index, failures_by_index

    started_at = time.monotonic()
    success_count = 0
    failure_count = failure_offset
    for completed_count, entry in enumerate(
        iter_completed_batch(
            target_items,
            lambda item: run_crawl_target(runner, item[1], issues),
            workers,
        ),
        start=1,
    ):
        index, target = entry.item
        url = str(target.get("url") or "")
        if entry.error is not None:
            found: list[CrawlResult] = []
            failure: CrawlFailure | None = CrawlFailure(
                url=url,
                name=str(target.get("name") or urlparse(url).netloc),
                reason=str(entry.error),
            )
        else:
            found, failure = entry.result or ([], None)

        results_by_index[index] = found
        failures_by_index[index] = failure
        if found and failure is None:
            success_count += 1
        else:
            failure_count += 1

        if on_progress is not None:
            on_progress(
                CrawlBatchProgress(
                    completed_count=completed_count,
                    done_count=done_offset + completed_count,
                    total=total,
                    success_count=success_count,
                    failure_count=failure_count,
                    elapsed_seconds=time.monotonic() - started_at,
                    index=index,
                    target=target,
                    results=found,
                    failure=failure,
                )
            )

    return results_by_index, failures_by_index
