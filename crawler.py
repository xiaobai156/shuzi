import argparse
import os
import uuid
from dataclasses import replace
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from run_lock import exclusive_run_lock
from kill_numbers.domain.models import (
    CrawlFailure,
    CrawlResult,
    RunStats,
    SourceDocument,
    DocumentParseResult,
)
from kill_numbers.acquisition.http_client import (
    HEADERS,
    HOST_MIN_INTERVAL,
    REQUEST_RETRIES,
    SSL_CONTEXT,
    TRANSIENT_ERROR_MARKERS,
    create_ssl_context,
    curl_fetch_bytes,
    decode_response,
    fetch_bytes,
    fetch_json,
    fetch_text,
    host_key,
    host_lock,
    is_http_404,
    is_retryable_network_error,
    wait_for_host_slot,
)
from kill_numbers.acquisition.browser_pool import render_page_documents
from kill_numbers.acquisition.policy import configured_response_limit
from kill_numbers.acquisition.documents import (
    discover_static_documents,
    document_debug_text,
    make_source_document,
    parseable_documents,
)
from kill_numbers.acquisition.discovery import (
    SCRIPT_WORKERS,
    decode_strdecode_payloads,
    iframe_urls,
    is_fetchable_script,
    script_urls,
)
from kill_numbers.acquisition.strategies.admin_article import (
    admin_article_api_url,
    admin_article_dict_to_content,
    crawl_admin_article_page as acquire_admin_article_page,
    crawl_configured_api_page,
    decode_possible_base64,
    fetch_admin_article_from_landing,
    find_admin_article_in_landing_data,
    parse_admin_article_id,
    site_url_param,
    validate_identity_article_api_data,
)
from kill_numbers.acquisition.strategies.user_page import (
    crawl_user_documents,
    crawl_user_page as acquire_user_page,
    parse_user_id,
)
from kill_numbers.acquisition.strategies.linked_pages import (
    exact_issue_link,
    fetch_encoded_page,
    fetch_unique_script_content,
    topic_links,
)
from kill_numbers.text_utils import (
    KEYWORD_NORMALIZATION_TABLE,
    as_list,
    clean_name,
    compact_text,
    extract_name_from_text,
    fullwidth_to_halfwidth,
    html_to_text,
    normalize_issue,
    normalize_keyword,
    origin,
    parse_issues,
    remove_fragment,
    unique_keep_order,
)
from kill_numbers.parsing.common import (
    BROAD_KEYWORD_MARKERS,
    CANDIDATE_REGION_WINDOW,
    resolve_candidate_window,
    candidate_rows,
    windowed_rows,
    all_issue_segment_matches,
    candidate_anchor_values,
    extract_issue_numbers,
    filter_candidates_by_region,
    find_anchor_index,
    find_last_anchor_index_before,
    find_number_groups,
    has_broad_keywords,
    has_duplicate_numbers,
    has_pending_open_marker,
    issue_position_window_starts,
    issue_segment_matches,
    issue_segments,
    iter_all_issue_segment_matches,
    needs_strict_region_window,
    normalize_region,
    scope_text_by_anchor,
    scope_text_by_anchor_with_offset,
    select_candidate,
    target_keywords,
    valid_number,
)
from kill_numbers.parsing.diagnostics import (
    detect_available_issues,
    diagnose_issue_mismatch,
    nearest_issues,
)
from kill_numbers.parsing.dedicated.site_parsers import (
    DEDICATED_TEN_ROW_CONTRACTS,
    dedicated_ten_available_issues,
    dedicated_ten_row_candidates,
    extract_chunyin_qiushe_bottom_10_numbers,
    extract_dedicated_ten_numbers,
    extract_fengwu_jiutian_bottom_10_numbers,
    extract_huxin_xiaozhu_stable_10_numbers,
    extract_identity_article_bottom_10_numbers,
    extract_macau_baoma_numbers,
    extract_majing_forum_bottom_10_numbers,
    extract_qiancai_liangde_bottom_10_numbers,
    extract_shanshui_xiangfeng_top_10_numbers,
    extract_top_article_history_current_cycle_numbers,
    extract_top_article_history_numbers,
    extract_white_tiger_stable_10_numbers,
    extract_xinzhu_forum_stable_10_numbers,
    extract_zuibaxian_top7_numbers,
    fengwu_jiutian_bottom_10_available_issues,
    fengwu_jiutian_bottom_10_section,
    huxin_xiaozhu_stable_10_available_issues,
    huxin_xiaozhu_stable_10_section,
    identity_article_bottom_10_available_issues,
    identity_article_bottom_10_candidates,
    normalize_identity_article_current_placeholder,
    qiancai_liangde_bottom_10_available_issues,
    qiancai_liangde_bottom_10_candidates,
    xinzhu_forum_top_candidates,
)
from kill_numbers.parsing.registry import (
    ACQUISITION_ONLY_PARSERS,
    VALID_SPECIAL_PARSERS,
    available_issues_from_content,
    parse_target_content,
)
from kill_numbers.validation.result_validator import validate_issue_map
from kill_numbers.parsing.source_scope import (
    VALID_SOURCE_TYPES,
    source_type_allowed,
    target_for_document,
)
from kill_numbers.parsing.errors import (
    CandidateConflictError,
    NoCandidateError,
    SourceContractError,
)
from kill_numbers.application.crawl_service import (
    CrawlBatchProgress,
    CrawlDependencies,
    run_crawl_batch,
    run_formal_crawl_target,
)
from kill_numbers.infrastructure.cache_repository import (
    update_recent_duplicate_cache as update_recent_cache_storage,
)
from kill_numbers.infrastructure.debug_repository import (
    debug_file_for as storage_debug_file_for,
    safe_filename as storage_safe_filename,
    save_debug_page as save_debug_page_storage,
)
from kill_numbers.infrastructure.file_store import atomic_write_text, atomic_write_json
from kill_numbers.infrastructure.run_manifest import write_run_manifest
from kill_numbers.domain.periods import (
    cycle_key,
    canonical_url,
    validate_cycle_lengths,
    target_identity,
    merge_cycle_lengths,
    target_cycle_lengths,
)
from kill_numbers.infrastructure.output_repository import (
    backup_existing_outputs as backup_outputs_storage,
    cleanup_old_backups as cleanup_old_backups_storage,
    output_files_for_issues as output_files_for_issues_storage,
    remove_stale_file,
)
from kill_numbers.infrastructure.target_repository import read_target_data, write_target_data


# 正式入口不提供默认期数；必须显式输入或传入 --issues。
DEFAULT_ISSUES = ""
DEFAULT_WORKERS = 8
DEFAULT_RETRY_PASSES = 1
RETRY_PASS_WAIT = 12
NO_ISSUE_MARKERS = (
    "没有找到指定期数",
    "没有识别到可用期数",
)
LOCAL_BLOCK_MARKERS = (
    "WinError 10013",
)
BACKUP_KEEP = 10


SCRIPT_DIR = Path(__file__).resolve().parent
TARGETS_FILE = SCRIPT_DIR / "targets.json"
DEBUG_DIR = SCRIPT_DIR / "debug_pages"
DEFAULT_RESULTS_DIR = Path(r"C:\Users\Administrator\Desktop\每天工具\爬虫合集\大围杀号生肖数据统一归纳")
RESULTS_DIR = Path(os.environ.get("SHUZI_RESULTS_DIR", str(DEFAULT_RESULTS_DIR))).expanduser()
CACHE_STATE_FILE = SCRIPT_DIR / ".cache-state.json"


def manifest_path_for_issue(issue):
    return RESULTS_DIR / f"{normalize_issue(issue)}期-杀数字-运行.json"



def configure_output_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# 默认输出文件；实际运行时会按期数生成，例如 128期-杀数字-成功.txt。
RESULT_FILE = "当期-杀数字-成功.txt"
FAILED_FILE = "当期-杀数字-失败.txt"
CACHE_FILE = SCRIPT_DIR / "recent_10_cache.json"
CACHE_UPDATE_FAILED_EXIT_CODE = 3
CACHE_SUCCESS_RATE_THRESHOLD_PERCENT = 85
VALID_TARGET_REGIONS = {"top", "bottom", "上", "下", "顶部", "尾部", "底部"}
# 正式目标必须有明确名称；自动作者信息只作为来源证据。
def load_targets(path: Path = TARGETS_FILE) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"目标配置文件不存在：{path}")
    data = read_target_data(path)
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict) or not item.get("url"):
            raise ValueError(f"targets.json 第 {index} 条缺少 url")
        special_parser = item.get("special_parser")
        if item.get('content_class') is not None and (
            item['content_class'] not in {'content', 'topic-content', 'd-content'}
            or special_parser or item.get('stop_anchor') or not item.get('anchor')
        ):
            raise ValueError(f"targets.json 第 {index} 条正文容器契约无效")
        allowed_source_types = item.get("allowed_source_types")
        if allowed_source_types is not None and (
            not isinstance(allowed_source_types, list)
            or not allowed_source_types
            or any(
                not isinstance(source_type, str)
                or source_type not in VALID_SOURCE_TYPES
                for source_type in allowed_source_types
            )
            or len(set(allowed_source_types)) != len(allowed_source_types)
        ):
            raise ValueError(
                f"targets.json 第 {index} 条 allowed_source_types 必须是唯一的受支持来源类型列表"
            )
        if special_parser is not None and (
            not isinstance(special_parser, str)
            or special_parser not in VALID_SPECIAL_PARSERS
        ):
            raise ValueError(f"targets.json 第 {index} 条 special_parser 非法：{special_parser}")
        region = item.get("region")
        if not isinstance(region, str) or not region.strip() or region not in VALID_TARGET_REGIONS:
            raise ValueError(
                f"targets.json 第 {index} 条必须配置合法 region：top/bottom/上/下/顶部/尾部"
            )
        resolve_candidate_window(item.get("issue_position_window"))
        configured_response_limit(item.get("max_response_bytes"))
        if special_parser in {"zuibaxian_top7", "fengwu_jiutian_bottom_10"} and item.get("issue_position_window", 3) != 3:
            raise ValueError("该专属解析器固定要求三条窗口，不能配置其他值")
        hosts = item.get("allowed_resource_hosts", [])
        if not isinstance(hosts, list) or any(not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9.-]+", host) for host in hosts):
            raise ValueError("allowed_resource_hosts 必须是明确主机名列表，不能含URL或通配符")
        if "browser_ready_selector" in item and not isinstance(item["browser_ready_selector"], str):
            raise ValueError("browser_ready_selector 必须是字符串")
        for flag in ("disabled", "allow_ambiguous", "allow_duplicate_numbers", "browser", "browser_fallback", "insecure_tls"):
            if flag in item and type(item[flag]) is not bool:
                raise ValueError(f"targets.json 第 {index} 条 {flag} 必须是布尔值")
        if "position" in item and item["position"] not in {"first", "last"}:
            raise ValueError(f"targets.json 第 {index} 条 position 只能是 first 或 last")
        if item.get("browser") is True and allowed_source_types and "browser" not in allowed_source_types:
            raise ValueError(
                f"targets.json 第 {index} 条 browser=true 时 allowed_source_types 必须允许 browser"
            )
        for field in ("anchor", "stop_anchor"):
            value = item.get(field)
            if value is not None and not (isinstance(value, str) or
                    isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)):
                raise ValueError(f"targets.json 第 {index} 条 {field} 格式错误")
        if item.get("source_url_pattern"):
            re.compile(item["source_url_pattern"])
        if item.get("cycle_id"):
            cycle_key(item["cycle_id"])
        if "cycle_lengths" in item:
            validate_cycle_lengths(item["cycle_lengths"])
        if urlparse(item["url"]).scheme not in {"http", "https"}:
            raise ValueError(f"targets.json 第 {index} 条 URL 只允许 http/https")
        if not isinstance(item.get("name"), str) or not item["name"].strip():
            raise ValueError(f"targets.json 第 {index} 条必须配置明确 name")
        count = item.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            raise ValueError(f"targets.json 第 {index} 条 count 必须是正整数")
        keywords = item.get("keywords")
        if keywords is not None and (
            not isinstance(keywords, list)
            or any(not isinstance(keyword, str) for keyword in keywords)
        ):
            raise ValueError(f"targets.json 第 {index} 条 keywords 必须是字符串列表")
        if special_parser == "top_article_history":
            if (
                normalize_region(region) not in {"top", "bottom"}
                or not item.get("anchor")
                or not item.get("article_title_anchor")
            ):
                raise ValueError(
                    f"targets.json 第 {index} 条 top_article_history 必须配置 top/bottom、anchor、article_title_anchor"
                )
        if special_parser == "top_article_history_current_cycle":
            if (
                normalize_region(region) not in {"top", "bottom"}
                or item.get("keywords") != ["准杀八码"]
                or count != 8
                or item.get("anchor") != "不可或缺 发表于"
                or item.get("stop_anchor") != "上一篇:"
                or item.get("article_title_anchor") != "频果报论坛准杀八码"
            ):
                raise ValueError(
                    f"targets.json 第 {index} 条 top_article_history_current_cycle 必须配置 "
                    "不可或缺专属 top/bottom、准杀八码、count=8、标题/正文边界"
                )
        if special_parser == "huxin_xiaozhu_stable_10":
            if region != "top" or not item.get("anchor") or count != 10:
                raise ValueError(
                    f"targets.json 第 {index} 条 huxin_xiaozhu_stable_10 必须配置 top、anchor、count=10"
                )
        if special_parser == "babu_maoge_must_ten":
            if region != "top" or not item.get("anchor") or "必杀十码" not in (item.get("keywords") or []):
                raise ValueError(
                    f"targets.json 第 {index} 条 babu_maoge_must_ten 必须配置 top、anchor、必杀十码"
                )
        if special_parser == "xinzhu_forum_stable_10":
            if region != "top" or not item.get("anchor") or count != 10:
                raise ValueError(
                    f"targets.json 第 {index} 条 xinzhu_forum_stable_10 必须配置 top、anchor、count=10"
                )
        dedicated_regions = {
            "shita_top_10": "top",
            "shanshui_xiangfeng_top_10": "top",
            "majing_forum_bottom_10": "bottom",
            "chunyin_qiushe_bottom_10": "bottom",
        }
        if special_parser in dedicated_regions:
            expected_region = dedicated_regions[special_parser]
            if region != expected_region or not item.get("anchor") or count != 10:
                raise ValueError(
                    f"targets.json 第 {index} 条 {special_parser} 必须配置 "
                    f"{expected_region}、anchor、count=10"
                )
        if special_parser == "shita_top_10":
            if (
                item.get("anchor") != "大家发(绝杀10码)"
                or item.get("stop_anchor") != "最早发表在"
            ):
                raise ValueError(
                    f"targets.json 第 {index} 条 shita_top_10 必须配置 "
                    "大家发专属起止锚点"
                )
        if special_parser == "identity_article_bottom_10":
            if (
                region != "bottom"
                or count != 10
                or (
                    not item.get("api_url")
                    and "/article/admin/" not in str(item.get("url") or "")
                )
                or not item.get("anchor")
                or not item.get("article_identity")
                or item.get("anchor") != item.get("article_identity")
            ):
                raise ValueError(
                    f"targets.json 第 {index} 条 identity_article_bottom_10 必须配置 "
                    "bottom、count=10、接口或admin文章入口、相同的 "
                    "anchor/article_identity"
                )
        if special_parser == "identity_article_top_10":
            if (
                region != "top"
                or count != 10
                or not item.get("anchor")
                or not item.get("article_identity")
                or item.get("anchor") != item.get("article_identity")
            ):
                raise ValueError(
                    f"targets.json 第 {index} 条 identity_article_top_10 必须配置 "
                    "top、count=10、相同的 anchor/article_identity"
                )
        if special_parser == "ttss_paginated_identity_top_10":
            link_keywords = item.get("link_keywords")
            pagination_limit = item.get("pagination_limit")
            if (
                region != "top"
                or count != 10
                or not item.get("anchor")
                or not item.get("article_identity")
                or item.get("anchor") != item.get("article_identity")
                or not isinstance(link_keywords, list)
                or not link_keywords
                or any(not isinstance(keyword, str) or not keyword.strip() for keyword in link_keywords)
                or not isinstance(pagination_limit, int)
                or isinstance(pagination_limit, bool)
                or pagination_limit <= 0
                or not item.get("pagination_next_text")
                or not str(item.get("url") or "").lower().endswith("page=1")
            ):
                raise ValueError(
                    f"targets.json 第 {index} 条 ttss_paginated_identity_top_10 必须配置 "
                    "page=1列表地址、top、count=10、分页、link_keywords和相同身份锚点"
                )
        if special_parser == "fengwu_jiutian_bottom_10":
            if (
                region != "bottom"
                or count != 10
                or item.get("anchor") != "（凤舞九天•绝杀10码）"
                or item.get("stop_anchor") != "（天上地下•绝杀半波）"
            ):
                raise ValueError(
                    f"targets.json 第 {index} 条 fengwu_jiutian_bottom_10 必须配置 "
                    "bottom、count=10、凤舞九天专属起止锚点"
                )
        if special_parser == "qiancai_liangde_bottom_10":
            if (
                region != "bottom"
                or count != 10
                or item.get("anchor") != "澳彩总站"
                or item.get("stop_anchor") != "提示!"
                or item.get("article_identity") != "钱彩两得"
                or item.get("encoding") != "gb18030"
            ):
                raise ValueError(
                    f"targets.json 第 {index} 条 qiancai_liangde_bottom_10 必须配置 "
                    "bottom、count=10、钱彩两得专属身份/边界、gb18030"
                )
    active = [item for item in data if not item.get("disabled")]
    for field in ("name",):
        values = [canonical_url(item[field]) if field == "url" else item[field].strip() for item in active]
        if len(set(values)) != len(values):
            raise ValueError(f"启用目标 {field} 不唯一，不能安全关联 TXT/缓存")
    if len({target_identity(t) for t in active}) != len(active):
        raise ValueError("启用目标栏目身份不唯一")
    return active


def save_targets(targets: list[dict], path: Path = TARGETS_FILE) -> None:
    write_target_data(path, targets)


TARGETS = load_targets()


















































































































def crawl_qiancai_liangde_page(target: dict) -> tuple[str, str]:
    encoding = str(target.get("encoding") or "")
    if encoding != "gb18030":
        raise ValueError("钱彩两得专属页面编码必须是 gb18030")
    content = fetch_encoded_page(str(target["url"]), encoding)
    return str(target.get("article_identity") or target.get("name") or "钱彩两得"), content






























def _single_issue_from_link_label(label: str) -> str | None:
    matches = unique_keep_order(
        normalize_issue(match.group(1))
        for match in re.finditer(r"(?<!\d)0?(\d{1,3})\s*期(?!\d)", label)
    )
    if not matches:
        return None
    if len(matches) != 1:
        raise SourceContractError(f"列表链接同时包含多个期号：{label}")
    return matches[0]


def _ordered_issue_links_from_documents(
    documents: list[SourceDocument],
    *,
    link_keywords: list[str],
) -> tuple[list[str], dict[str, str]]:
    """Collect unique period links in source order, rejecting per-period URL conflicts."""
    candidates: dict[str, list[str]] = {}
    ordered: list[str] = []
    parseable = sorted(
        parseable_documents(documents),
        key=lambda document: document.priority,
        reverse=True,
    )
    for document in parseable:
        for href, label in topic_links(document.content):
            compact = normalize_keyword(label)
            if not all(normalize_keyword(keyword) in compact for keyword in link_keywords):
                continue
            issue = _single_issue_from_link_label(label)
            if not issue:
                continue
            absolute = urljoin(document.url, href)
            urls = candidates.setdefault(issue, [])
            if absolute not in urls:
                urls.append(absolute)
            if issue not in ordered:
                ordered.append(issue)
    conflicts = {issue: urls for issue, urls in candidates.items() if len(urls) > 1}
    if conflicts:
        issue, urls = next(iter(conflicts.items()))
        raise CandidateConflictError(f"{issue}期列表链接候选冲突：{urls}")
    return ordered, {issue: urls[0] for issue, urls in candidates.items()}


def _directional_acquisition_issues(ordered: list[str], target: dict) -> list[str]:
    if target.get("_history_discovery") is True:
        window = resolve_candidate_window(target.get("_history_depth"))
    else:
        window = resolve_candidate_window(target.get("issue_position_window"))
    region = normalize_region(target.get("region"))
    if region == "top":
        return ordered[:window]
    if region == "bottom":
        return ordered[-window:]
    raise SourceContractError("采集专属列表必须配置 top 或 bottom")


def _zuojianzifu_issue_links(target: dict) -> tuple[list[str], dict[str, str]]:
    _name, documents = discover_static_documents(str(target["url"]))
    ordered, links = _ordered_issue_links_from_documents(
        documents,
        link_keywords=["作茧自缚"],
    )
    if not ordered:
        raise NoCandidateError("作茧自缚列表没有找到任何专属期号链接")
    return ordered, links


def _ttss_current_identity_article(target: dict) -> tuple[str, str]:
    """Resolve the newest identity article, then let the article own period history.

    TTSS reuses one article for multiple periods and updates the listing label to
    the newest period.  Therefore the list is an identity locator, not a period
    archive.  We still keep the configured pagination cap as a hard contract and
    reject duplicate URLs for the same top identity row.
    """
    page_limit = target.get("pagination_limit")
    if isinstance(page_limit, bool) or not isinstance(page_limit, int) or page_limit <= 0:
        raise SourceContractError(f"分页上限无效：{page_limit}")
    next_text = str(target.get("pagination_next_text") or "").strip()
    link_keywords = [
        str(keyword).strip()
        for keyword in (target.get("link_keywords") or [])
        if str(keyword).strip()
    ]
    if not next_text or not link_keywords:
        raise SourceContractError("分页身份文章探测缺少 next_text 或 link_keywords")
    if normalize_region(target.get("region")) != "top":
        raise SourceContractError("分页身份文章当前定位只支持 top")

    current_url = remove_fragment(str(target["url"]))
    visited_urls: set[str] = set()
    for page_number in range(1, page_limit + 1):
        if current_url in visited_urls:
            raise SourceContractError(f"分页链接循环：{current_url}")
        visited_urls.add(current_url)
        _name, documents = discover_static_documents(current_url)
        page_issues, page_links = _ordered_issue_links_from_documents(
            documents,
            link_keywords=link_keywords,
        )
        if page_issues:
            current_issue = page_issues[0]
            return current_issue, page_links[current_issue]

        next_candidates: list[str] = []
        for document in parseable_documents(documents):
            next_candidates.extend(
                urljoin(document.url, href)
                for href, label in topic_links(document.content)
                if label.strip() == next_text
            )
        distinct_next = list(dict.fromkeys(next_candidates))
        if len(distinct_next) > 1:
            raise CandidateConflictError(
                f"第{page_number}页分页下一页链接冲突：{distinct_next}"
            )
        if not distinct_next or not distinct_next[0].strip() or distinct_next[0].strip() == "#":
            break
        next_url = remove_fragment(urljoin(current_url, distinct_next[0]))
        if next_url == current_url:
            break
        current_url = next_url

    raise NoCandidateError(
        f"分页上限 {page_limit} 内没有找到当前身份文章"
    )

def _acquisition_issue_links(target: dict) -> tuple[list[str], dict[str, str]]:
    parser_name = str(target.get("special_parser") or "")
    if parser_name == "zuojianzifu_link_chain":
        return _zuojianzifu_issue_links(target)
    raise SourceContractError(f"不支持的采集专属历史探测器：{parser_name}")


def available_issues_for_acquisition_target(target: dict) -> list[str]:
    """Discover the strict current/history window for acquisition-only targets."""
    parser_name = str(target.get("special_parser") or "")
    if parser_name == "ttss_paginated_identity_top_10":
        _listing_issue, article_url = _ttss_current_identity_article(target)
        _article_name, documents = discover_static_documents(article_url)
        article_target = dict(target)
        article_target["special_parser"] = "identity_article_top_10"
        available, _selected = available_issues_for_documents(
            documents,
            article_target,
        )
        if not available:
            raise NoCandidateError("当前身份文章没有找到合法期号数据")
        return available

    ordered, _links = _acquisition_issue_links(target)
    return _directional_acquisition_issues(ordered, target)


def crawl_zuojianzifu_link_chain(
    target: dict,
    issues: list[str],
) -> tuple[str, str, dict[str, list[str]], dict[str, SourceDocument]]:
    if not issues:
        raise ValueError("没有指定期数")
    requested = unique_keep_order(normalize_issue(issue) for issue in issues)
    ordered, links = _zuojianzifu_issue_links(target)
    allowed = set(_directional_acquisition_issues(ordered, target))
    outside = [issue for issue in requested if issue not in allowed]
    if outside:
        raise NoCandidateError(
            "指定期数不在作茧自缚配置方向窗口内："
            + ",".join(f"{issue}期" for issue in outside)
        )

    article_names = []
    article_documents: list[SourceDocument] = []
    article_documents_by_issue: dict[str, SourceDocument] = {}
    issue_map: dict[str, list[str]] = {}
    for normalized_issue in requested:
        article_url = links[normalized_issue]
        article_name, documents = discover_static_documents(article_url)
        article_target = dict(target)
        article_target["special_parser"] = ""
        found, selected_document = parse_target_documents(
            documents,
            article_target,
            [normalized_issue],
        )
        if normalized_issue not in found:
            raise ValueError(f"{normalized_issue}期专属文章没有找到符合配置的号码")
        if selected_document is None:
            raise ValueError(f"{normalized_issue}期专属文章缺少来源文档证据")
        issue_map[normalized_issue] = found[normalized_issue]
        article_names.append(article_name)
        article_documents.append(selected_document)
        article_documents_by_issue[normalized_issue] = selected_document
    return (
        article_names[0],
        document_debug_text(article_documents),
        issue_map,
        article_documents_by_issue,
    )


def crawl_ttss_paginated_identity_top_10(
    target: dict,
    issues: list[str],
) -> tuple[str, str, dict[str, list[str]], dict[str, SourceDocument]]:
    if not issues:
        raise ValueError("没有指定期数")
    requested = unique_keep_order(normalize_issue(issue) for issue in issues)

    _listing_issue, article_url = _ttss_current_identity_article(target)
    article_name, documents = discover_static_documents(article_url)
    article_target = dict(target)
    article_target["special_parser"] = "identity_article_top_10"
    found, selected_document = parse_target_documents(
        documents,
        article_target,
        requested,
    )
    missing = [issue for issue in requested if issue not in found]
    if missing:
        raise NoCandidateError(
            "指定期数不在当前身份文章配置顶部窗口内："
            + ",".join(f"{issue}期" for issue in missing)
        )
    if selected_document is None:
        raise ValueError("当前身份文章缺少来源文档证据")

    issue_map = {issue: found[issue] for issue in requested}
    article_documents_by_issue = {
        issue: selected_document for issue in requested
    }
    return (
        article_name,
        document_debug_text([selected_document]),
        issue_map,
        article_documents_by_issue,
    )

def admin_content_matches_target(content: str, target: dict | None, issues: list[str] | None) -> bool:
    if not target or not issues:
        return bool(content.strip())
    try:
        return bool(parse_target_content(content, target, issues))
    except NoCandidateError:
        return False


def crawl_admin_article_page(
    url: str,
    target: dict | None = None,
    issues: list[str] | None = None,
) -> tuple[str, str]:
    def render_target_document(render_url: str) -> str:
        documents = render_page_documents(render_url)
        matched: list[tuple[SourceDocument, dict[str, list[str]]]] = []
        for document in sorted(
            parseable_documents(documents),
            key=lambda item: item.priority,
            reverse=True,
        ):
            if admin_content_matches_target(document.content, target, issues):
                try:
                    issue_map = parse_target_content(document.content, target, issues or [])
                except NoCandidateError:
                    continue
                matched.append((document, issue_map))
        if matched:
            values = {
                tuple(
                    (issue, tuple(numbers))
                    for issue, numbers in sorted(issue_map.items())
                )
                for _document, issue_map in matched
            }
            if len(values) > 1:
                raise ValueError("浏览器渲染文档候选冲突，已停止输出避免抓错")
            return matched[0][0].content
        candidates = parseable_documents(documents)
        if not candidates:
            raise ValueError("浏览器渲染没有生成可解析文档")
        return candidates[0].content

    return acquire_admin_article_page(
        url,
        target,
        issues,
        content_matches=admin_content_matches_target,
        render_page=render_target_document,
    )


def crawl_user_page(
    url: str,
    issues: list[str],
    keywords: list[str] | None = None,
    expected_count: int | None = None,
    position: str = "first",
    allow_duplicate_numbers: bool = False,
    anchor=None,
    stop_anchor=None,
    region: str | None = None,
    issue_position_window: int | None = None,
) -> tuple[str, str]:
    def extract_collected_issues(content: str) -> dict[str, list[str]]:
        return extract_issue_numbers(
            content,
            issues,
            keywords=keywords,
            expected_count=expected_count,
            position=position,
            allow_duplicate_numbers=allow_duplicate_numbers,
            anchor=anchor,
            stop_anchor=stop_anchor,
            region=region,
            issue_position_window=issue_position_window,
        )
    return acquire_user_page(
        url,
        issues,
        extract_issues=extract_collected_issues,
        batch_after_forum_page=normalize_region(region) == "top",
        fetch_json_value=fetch_json,
    )


def safe_filename(value: str, limit: int = 80) -> str:
    return storage_safe_filename(value, limit)


def debug_file_for(target: dict, issues: list[str], name: str) -> Path:
    return storage_debug_file_for(DEBUG_DIR, target, issues, name, normalize_issue)


def save_debug_page(target: dict, issues: list[str], name: str, content: str, reason: str) -> Path | None:
    return save_debug_page_storage(
        DEBUG_DIR,
        target,
        issues,
        name,
        content,
        reason,
        normalize_issue,
        atomic_write_text,
    )


def _document_conflict_error(
    issue: str,
    candidates: list[tuple[SourceDocument, list[str]]],
) -> ValueError:
    previews = [
        f"{document.kind}@{document.url}: {','.join(numbers)}"
        for document, numbers in candidates
    ]
    return CandidateConflictError(
        f"{issue}期 跨文档候选冲突，已停止输出避免抓错：" + " | ".join(previews)
    )


def _without_redundant_reconstructed_fragments(documents):
    """Drop only script fragments already proven inside a complete reconstructed page.

    ``rendered_script_page`` is emitted by acquisition only after every directly
    referenced ``/upload/script/`` content script was statically replayed in the
    browser's source order.  Parsing those same scripts and decoded components a
    second time can turn a harmless partial fragment (for example, an anchor
    without its later stop marker) into a hard source-contract failure.

    Independent documents remain untouched.  If no complete reconstruction is
    present, every fragment continues to participate and fail closed as before.
    """
    documents = list(documents)
    covered_scripts = set()
    for document in documents:
        if document.kind != "rendered_script_page":
            continue
        for source_url in document.metadata.get("script_sources", []):
            if isinstance(source_url, str) and source_url.strip():
                covered_scripts.add(remove_fragment(source_url.strip()))
    if not covered_scripts:
        return documents

    result = []
    for document in documents:
        if (
            document.kind == "external_script"
            and remove_fragment(document.url) in covered_scripts
        ):
            continue
        if (
            document.kind == "decoded_script_component"
            and remove_fragment(document.parent_url) in covered_scripts
        ):
            continue
        result.append(document)
    return result


def _directional_parseable_documents(documents, target):
    """Rank valid rows across a user-post sequence without joining documents."""
    parseable = parseable_documents(_without_redundant_reconstructed_fragments(documents))
    sequences = {}
    ordinary = []
    for document in parseable:
        sequence = document.metadata.get("region_sequence")
        index = document.metadata.get("region_index")
        if sequence and type(index) is int:
            sequences.setdefault(sequence, []).append(document)
        else:
            ordinary.append(document)
    if not sequences:
        return parseable
    result = list(ordinary)
    for group in sequences.values():
        rows = []
        for document in sorted(group, key=lambda doc: doc.metadata["region_index"]):
            try:
                effective = target_for_document(target, document)
                section, _ = scope_text_by_anchor_with_offset(
                    html_to_text(document.content),
                    effective.get("anchor"),
                    effective.get("stop_anchor"),
                )
            except NoCandidateError:
                continue
            for row in candidate_rows(
                section,
                effective.get("keywords"),
                effective.get("count"),
                effective.get("allow_duplicate_numbers", False),
            ):
                rows.append((document, row))
        business_selected = windowed_rows(
            rows,
            target.get("region"),
            target.get("issue_position_window"),
        )
        selected = (
            windowed_rows(
                rows,
                target.get("region"),
                target.get("_history_depth"),
            )
            if target.get("_history_discovery") is True
            else business_selected
        )
        ranks = {}
        for rank, (document, row) in enumerate(selected):
            ranks.setdefault(id(document), {})[row.start] = rank
        current_starts = {}
        for document, row in business_selected:
            current_starts.setdefault(id(document), []).append(row.start)
        for document in group:
            if id(document) in ranks:
                result.append(
                    replace(
                        document,
                        metadata={
                            **document.metadata,
                            "allowed_row_starts": list(ranks[id(document)]),
                            "current_allowed_row_starts": current_starts.get(
                                id(document), []
                            ),
                            "current_window_verified": bool(business_selected),
                            "row_ranks": ranks[id(document)],
                            "selected_row_count": len(selected),
                        },
                    )
                )
    return result


def parse_target_document_results(
    documents: list[SourceDocument],
    target: dict,
    issues: list[str],
) -> DocumentParseResult:
    """Merge independent documents per issue while retaining exact provenance."""
    parseable = parseable_documents(documents)
    source_url_pattern = str(target.get("source_url_pattern") or "").strip()
    if source_url_pattern:
        parseable = [
            document
            for document in parseable
            if re.search(source_url_pattern, document.url, re.I)
        ]
        if not parseable:
            raise SourceContractError(
                f"没有找到专属来源文档：{source_url_pattern}"
            )
        if "source_anchor" in target:
            raw_source_anchor = target.get("source_anchor")
            if raw_source_anchor is None:
                raise SourceContractError("source_anchor 不能为 null")
            source_anchor = str(raw_source_anchor).strip()
            if source_anchor:
                target = {**target, "anchor": source_anchor}
            else:
                # An explicitly empty source_anchor means the configured source
                # URL itself is the inner identity boundary. This is allowed only
                # when URL + source-type contracts identify exactly one document.
                allowed = [
                    document
                    for document in parseable
                    if source_type_allowed(target, document)
                ]
                if len(allowed) != 1:
                    raise SourceContractError(
                        "空 source_anchor 仅允许唯一专属来源文档，"
                        f"当前候选 {len(allowed)} 个"
                    )
                verified_document = replace(
                    allowed[0],
                    metadata={
                        **allowed[0].metadata,
                        "source_url_identity_verified": True,
                    },
                )
                parseable = [
                    verified_document if document is allowed[0] else document
                    for document in parseable
                ]
                target = {
                    **target,
                    "anchor": "",
                    "_scope_kind": "source_url_identity",
                    "_source_identity": verified_document.url,
                }

    parseable = _directional_parseable_documents(parseable, target)
    document_order = {id(document): index for index, document in enumerate(parseable)}
    requested = list(dict.fromkeys(normalize_issue(issue) for issue in issues))
    requested_set = set(requested)
    parsed: list[tuple[SourceDocument, dict[str, list[str]]]] = []
    for document in parseable:
        try:
            document_target = target_for_document(target, document)
            issue_map = parse_target_content(document.content, document_target, requested)
        except NoCandidateError:
            continue
        unexpected = {normalize_issue(issue) for issue in issue_map} - requested_set
        if unexpected:
            raise SourceContractError(
                "解析器混入非指定期数："
                + ",".join(f"{issue}期" for issue in sorted(unexpected, key=int))
            )
        if issue_map:
            parsed.append((document, issue_map))

    if not parsed:
        return DocumentParseResult({}, {}, None)

    merged: dict[str, list[str]] = {}
    sources: dict[str, SourceDocument] = {}
    for issue in requested:
        candidates = [
            (document, issue_map[issue])
            for document, issue_map in parsed
            if issue in issue_map
        ]
        if not candidates:
            continue
        distinct = {tuple(numbers) for _document, numbers in candidates}
        if len(distinct) > 1:
            raise _document_conflict_error(issue, candidates)
        selected_document, selected_numbers = max(
            candidates,
            key=lambda item: (
                item[0].priority,
                -document_order[id(item[0])],
            ),
        )
        merged[issue] = list(selected_numbers)
        sources[issue] = selected_document

    if not merged:
        return DocumentParseResult({}, {}, None)
    source_coverage = {
        id(document): sum(1 for selected in sources.values() if selected is document)
        for document, _issue_map in parsed
    }
    primary = max(
        {id(document): document for document, _issue_map in parsed}.values(),
        key=lambda document: (
            source_coverage.get(id(document), 0),
            document.priority,
            -document_order[id(document)],
        ),
    )
    return DocumentParseResult(merged, sources, primary)


def parse_target_documents(
    documents: list[SourceDocument],
    target: dict,
    issues: list[str],
) -> tuple[dict[str, list[str]], SourceDocument | None]:
    """Compatibility wrapper; new callers should use per-issue provenance."""
    result = parse_target_document_results(documents, target, issues)
    return result.issue_map, result.primary_document


def contract_is_split_across_documents(
    documents: list[SourceDocument],
    target: dict,
) -> bool:
    anchor_values = [
        value
        for value in as_list(target.get("anchor"))
        if str(value).strip()
    ]
    stop_values = [
        value
        for value in as_list(target.get("stop_anchor"))
        if str(value).strip()
    ]
    if not anchor_values or not stop_values:
        return False

    anchor_documents = set()
    stop_documents = set()
    for document in parseable_documents(documents):
        normalized = normalize_keyword(html_to_text(document.content))
        if any(normalize_keyword(value) in normalized for value in anchor_values):
            anchor_documents.add(document.fingerprint)
        if any(normalize_keyword(value) in normalized for value in stop_values):
            stop_documents.add(document.fingerprint)
    return bool(anchor_documents and stop_documents and not anchor_documents & stop_documents)


def fetch_target_documents(
    target: dict,
    issues: list[str] | None = None,
) -> tuple[str, list[SourceDocument]]:
    url = str(target["url"])
    requested_issues = list(issues or [])
    if target.get("special_parser") in ACQUISITION_ONLY_PARSERS:
        raise ValueError(
            f"{target.get('special_parser')} 是链式采集专属解析器，不能走通用文档抓取"
        )
    if target.get("browser") is True:
        documents = render_page_documents(url)
        parseable = parseable_documents(documents)
        if not parseable:
            raise ValueError("browser=true 但真实 Chromium 没有生成可解析来源")
        name = extract_name_from_text(
            parseable[0].content,
            fallback=urlparse(url).netloc,
        )
        return name, documents
    if parse_admin_article_id(url):
        name, content = crawl_admin_article_page(url, target, requested_issues)
        return name, [
            make_source_document(
                kind="admin_article",
                url=url,
                content=content,
                priority=100,
                metadata={"parseable": True},
            )
        ]
    if target.get("api_url"):
        name, content = crawl_configured_api_page(url, target)
        return name, [
            make_source_document(
                kind="configured_api",
                url=str(target["api_url"]),
                parent_url=url,
                content=content,
                priority=100,
                metadata={"parseable": True},
            )
        ]
    if target.get("special_parser") == "babu_maoge_must_ten":
        content = fetch_unique_script_content(
            url,
            "/bbs/qsma.js",
            error_label="八步毛哥必杀十码",
        )
        return str(target.get("name") or "八步毛哥必杀"), [
            make_source_document(
                kind="dedicated_script",
                url=url,
                content=content,
                priority=100,
                metadata={"parseable": True},
            )
        ]
    if target.get("special_parser") == "qiancai_liangde_bottom_10":
        name, content = crawl_qiancai_liangde_page(target)
        return name, [
            make_source_document(
                kind="encoded_page",
                url=url,
                content=content,
                priority=100,
                metadata={"parseable": True},
            )
        ]
    if parse_user_id(url):
        return crawl_user_documents(url, fetch_json_value=fetch_json)

    return discover_static_documents(url)


def available_issues_for_documents(
    documents: list[SourceDocument],
    target: dict,
) -> tuple[list[str], SourceDocument | None]:
    """Aggregate independent source documents without hiding hard failures."""
    parseable = parseable_documents(documents)
    source_url_pattern = str(target.get("source_url_pattern") or "").strip()
    if source_url_pattern:
        parseable = [
            document
            for document in parseable
            if re.search(source_url_pattern, document.url, re.I)
        ]
        if not parseable:
            raise SourceContractError(
                f"没有找到专属来源文档：{source_url_pattern}"
            )
    parseable = _directional_parseable_documents(parseable, target)
    document_order = {id(document): index for index, document in enumerate(parseable)}
    candidates: list[tuple[SourceDocument, list[str]]] = []
    for document in parseable:
        try:
            document_target = target_for_document(target, document)
            if (
                document_target.get("_history_discovery") is True
                and document.metadata.get("current_window_verified") is not True
            ):
                current_target = dict(document_target)
                current_target.pop("_history_discovery", None)
                current_target.pop("_history_depth", None)
                current = available_issues_from_content(
                    document.content,
                    current_target,
                )
                if not current:
                    continue
            available = available_issues_from_content(
                document.content,
                document_target,
            )
        except NoCandidateError:
            continue
        if available:
            candidates.append((document, available))
    if not candidates:
        return [], None

    ordered = sorted(
        candidates,
        key=lambda item: (-item[0].priority, document_order[id(item[0])]),
    )
    available = unique_keep_order(
        issue
        for _document, issues in ordered
        for issue in issues
    )
    primary = max(
        candidates,
        key=lambda item: (
            len(item[1]),
            item[0].priority,
            -document_order[id(item[0])],
        ),
    )[0]
    return available, primary


def _crawl_dependencies() -> CrawlDependencies:
    return CrawlDependencies(
        fetch_target_documents=fetch_target_documents,
        parse_target_documents=parse_target_documents,
        crawl_zuojianzifu_link_chain=crawl_zuojianzifu_link_chain,
        contract_is_split_across_documents=contract_is_split_across_documents,
        save_debug_page=save_debug_page,
        crawl_ttss_paginated_identity_top_10=crawl_ttss_paginated_identity_top_10,
        parse_target_document_results=parse_target_document_results,
    )


def crawl_one(target: dict, issues: list[str]) -> tuple[list[CrawlResult], CrawlFailure | None]:
    """Legacy-compatible entry that delegates the formal pipeline to application."""
    return run_formal_crawl_target(_crawl_dependencies(), target, issues)


def fetch_target_content(target: dict, issues: list[str]) -> tuple[str, str]:
    if target.get("special_parser") == "zuojianzifu_link_chain":
        name, content, _issue_map, _documents = crawl_zuojianzifu_link_chain(target, issues)
        return name, content
    if target.get("special_parser") == "ttss_paginated_identity_top_10":
        name, content, _issue_map, _documents = crawl_ttss_paginated_identity_top_10(
            target,
            issues,
        )
        return name, content

    name, documents = fetch_target_documents(target, issues)
    selected_document = None
    if issues:
        _issue_map, selected_document = parse_target_documents(
            documents,
            target,
            issues,
        )
    else:
        _available, selected_document = available_issues_for_documents(
            documents,
            target,
        )
    if selected_document is None:
        candidates = sorted(
            parseable_documents(documents),
            key=lambda document: document.priority,
            reverse=True,
        )
        if not candidates:
            raise ValueError("没有可解析的独立来源文档")
        selected_document = candidates[0]
    return name, selected_document.content


def strict_target_extracts(content: str, target: dict, issues: list[str]) -> bool:
    try:
        found = extract_issue_numbers(
            content,
            issues,
            keywords=target.get("keywords"),
            expected_count=target.get("count"),
            position=target.get("position", "first"),
            strict_ambiguous=True,
            allow_duplicate_numbers=target.get("allow_duplicate_numbers", False),
            anchor=target.get("anchor"),
            stop_anchor=target.get("stop_anchor"),
            region=target.get("region"),
            issue_position_window=target.get("issue_position_window"),
        )
    except NoCandidateError:
        return False
    return bool(found)


def output_files_for_issues(issues: list[str]) -> tuple[str, str, str]:
    return output_files_for_issues_storage(
        RESULTS_DIR,
        issues,
        normalize_issue,
        RESULT_FILE,
        FAILED_FILE,
    )


def backup_existing_outputs(*paths: str) -> None:
    backup_outputs_storage(*paths, keep=BACKUP_KEEP)


def cleanup_old_backups(path: Path) -> None:
    cleanup_old_backups_storage(path, keep=BACKUP_KEEP)


def blocked_by_local_socket_policy(failures: list[CrawlFailure]) -> bool:
    return bool(failures) and all("WinError 10013" in item.reason for item in failures)


def failure_category(failure: CrawlFailure) -> str:
    reason = failure.reason
    if "手动风险拦截" in reason:
        return "手动风险拦截"
    if any(marker in reason for marker in LOCAL_BLOCK_MARKERS):
        return "本地网络权限"
    if any(marker in reason for marker in TRANSIENT_ERROR_MARKERS):
        return "网络临时失败"
    if any(marker in reason for marker in NO_ISSUE_MARKERS):
        return "页面无当期"
    if "位置在" in reason and "配置要求" in reason:
        return "位置不匹配"
    if "候选不唯一" in reason:
        return "解析歧义"
    if "没有找到正文锚点" in reason:
        return "锚点失效"
    return "其他失败"


def is_transient_failure(failure: CrawlFailure | None) -> bool:
    if not failure:
        return False
    return any(marker in failure.reason for marker in TRANSIENT_ERROR_MARKERS)


def crawl_progress_line(
    done_count: int,
    total: int,
    success_count: int,
    failure_count: int,
    elapsed_seconds: float,
    name: str,
) -> str:
    percent = int(done_count * 100 / total) if total else 100
    return (
        f"[进度 {done_count}/{total} {percent}% 成功 {success_count} "
        f"失败 {failure_count} 用时 {elapsed_seconds:.1f}s] 当前：{name}"
    )


def dedupe_results(results: list[CrawlResult]) -> list[CrawlResult]:
    seen = set()
    deduped = []
    for item in results:
        key = (item.url, item.name, item.issue, tuple(item.numbers))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def update_recent_duplicate_cache(
    cache_path: Path,
    results: list[CrawlResult],
    issues: list[str],
    recent_count: int = 10,
    failures: list[CrawlFailure] | None = None,
    *, targets=None, cycle_id=None, cycle_lengths=None,
) -> None:
    update_recent_cache_storage(
        cache_path,
        results,
        issues,
        recent_count,
        failures=failures, targets=targets, cycle_id=cycle_id, cycle_lengths=cycle_lengths,
    )


def persist_cache_after_realtime_result(
    cache_path: Path,
    results: list[CrawlResult],
    failures: list[CrawlFailure],
    issues: list[str],
    *, targets=None, cycle_id=None, cycle_lengths=None,
) -> str | None:
    """Persist cache after realtime outputs are finalized without changing them."""
    try:
        update_recent_duplicate_cache(
            cache_path,
            results,
            issues,
            recent_count=10,
            failures=failures, targets=targets, cycle_id=cycle_id, cycle_lengths=cycle_lengths,
        )
    except Exception as exc:
        return str(exc)
    return None


def should_update_cache_for_success_rate(
    total_targets: int,
    successful_targets: int,
) -> bool:
    """Return whether the active-target success rate is strictly above 85%."""
    if total_targets <= 0 or successful_targets < 0:
        return False
    return successful_targets * 100 > total_targets * CACHE_SUCCESS_RATE_THRESHOLD_PERCENT


def summarize_failures(failures: list[CrawlFailure]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for failure in failures:
        category = failure_category(failure)
        summary[category] = summary.get(category, 0) + 1
    return summary


def crawl_targets(
    target_items: list[tuple[int, dict]],
    issues: list[str],
    max_workers: int,
    label: str = "",
    done_offset: int = 0,
    failure_offset: int = 0,
    total_override: int | None = None,
) -> tuple[dict[int, list[CrawlResult]], dict[int, CrawlFailure | None]]:
    if label:
        print(label)

    def print_progress(progress: CrawlBatchProgress) -> None:
        target = progress.target
        name = target.get("name") or urlparse(target["url"]).netloc
        print(
            crawl_progress_line(
                progress.done_count,
                progress.total,
                progress.success_count,
                progress.failure_count,
                progress.elapsed_seconds,
                name,
            ),
            flush=True,
        )
        if not progress.results or progress.failure:
            if progress.failure:
                print(
                    f"  失败[{failure_category(progress.failure)}]："
                    f"{progress.failure.name} {target['url']}，原因：{progress.failure.reason}"
                )
            else:
                print(f"  失败[未知]：{target['url']}，原因：没有返回数据")

    return run_crawl_batch(
        target_items,
        issues,
        max_workers,
        crawl_one,
        done_offset=done_offset,
        failure_offset=failure_offset,
        total_override=total_override,
        on_progress=print_progress,
    )


def write_outputs(
    results: list[CrawlResult],
    failures: list[CrawlFailure],
    result_file: str,
    failed_file: str,
    report_file: str,
    stats: RunStats,
    skip_backup: bool = False,
) -> None:
    if not skip_backup:
        backup_existing_outputs(result_file, report_file)
    if failures and not skip_backup:
        backup_existing_outputs(failed_file)

    result_lines = [
        f"{','.join(item.numbers)} {item.name}"
        for item in dedupe_results(results)
    ]
    atomic_write_text(result_file, "\n".join(result_lines) + ("\n" if result_lines else ""))

    if failures:
        failure_lines = [
            f"[{failure_category(item)}] {item.name} {item.url} {item.reason}"
            for item in failures
        ]
        atomic_write_text(failed_file, "\n\n".join(failure_lines) + "\n")
    else:
        remove_stale_failure_file(failed_file, skip_backup=skip_backup)

    write_report(results, failures, report_file, stats)


def remove_stale_failure_file(failed_file: str, skip_backup: bool = False) -> None:
    path = Path(failed_file)
    if not path.exists():
        return
    if not skip_backup:
        backup_existing_outputs(failed_file)
    remove_stale_file(path)


def write_report(
    results: list[CrawlResult],
    failures: list[CrawlFailure],
    report_file: str,
    stats: RunStats,
) -> None:
    unique_results = dedupe_results(results)
    failure_summary = summarize_failures(failures)
    lines = [
        "抓取报告",
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"目标总数：{stats.total_targets}",
        f"初跑成功：{stats.initial_success}",
        f"补抓救回：{stats.retry_rescued}",
        f"最终成功：{len(unique_results)}",
        f"最终失败：{len(failures)}",
        f"并发线程：{stats.workers}",
        f"补抓轮数：{stats.retry_passes_used}",
        "",
        "失败分类：",
    ]
    if failure_summary:
        for category, count in sorted(failure_summary.items()):
            lines.append(f"- {category}：{count}")
    else:
        lines.append("- 无")

    if failures:
        lines.extend(["", "失败明细："])
        for failure in failures:
            lines.append(f"- [{failure_category(failure)}] {failure.name} {failure.url}")
            lines.append(f"  原因：{failure.reason}")

    atomic_write_text(report_file, "\n".join(lines) + "\n")


def _main_unlocked() -> int:
    configure_output_encoding()
    parser = argparse.ArgumentParser(description="按指定期数爬取号码并输出 txt")
    parser.add_argument(
        "--issues",
        default=DEFAULT_ISSUES,
        help="指定单一期数，例如119；多个期数请使用多期入口",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help="并发线程数，默认 8；这些站点不稳定，失败多时可手动调低",
    )
    parser.add_argument(
        "--retry-passes",
        type=int,
        default=DEFAULT_RETRY_PASSES,
        help="网络失败后的慢速补抓轮数，默认 1；设为 0 可关闭",
    )
    parser.add_argument(
        "--no-cache-update",
        action="store_true",
        help="不更新 recent_10_cache.json；多期独立抓取专用",
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--cycle-id", default=os.environ.get("SHUZI_CYCLE_ID", ""))
    parser.add_argument("--previous-cycle-length", type=int, default=None)
    args = parser.parse_args()
    skip_output_backup = any(arg == "--issues" or arg.startswith("--issues=") for arg in sys.argv[1:])
    try:
        issues = parse_issues(args.issues)
        cycle = cycle_key(args.cycle_id)
        command_lengths = {}
        if args.previous_cycle_length is not None:
            if not cycle or int(cycle) <= 1:
                raise ValueError("上一周期长度必须同时提供明确的 --cycle-id")
            command_lengths = validate_cycle_lengths(
                {str(int(cycle) - 1): args.previous_cycle_length}
            )
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))
    if len(issues) > 1:
        parser.error("正式单期入口只允许一个期数，请使用多期入口逐期执行")
    if args.workers <= 0 or args.retry_passes < 0:
        parser.error("workers 必须为正整数，retry-passes 不能为负数")
    if not issues:
        print("请指定期数，例如：python crawler.py --issues 119")
        return 2

    # Risk checks belong to the formal target pipeline.  This entrypoint must
    # not fetch risky sites twice or mutate targets.json during a run.
    try:
        active_targets = []
        configured_cycles = set()
        for raw_target in TARGETS:
            target = dict(raw_target)
            configured_cycle = cycle_key(target.get("cycle_id"))
            if cycle and configured_cycle and cycle != configured_cycle:
                raise ValueError(
                    f"{target.get('name') or target.get('url')} 的 cycle_id "
                    f"{configured_cycle} 与命令行 {cycle} 冲突"
                )
            selected_cycle = cycle or configured_cycle
            if selected_cycle:
                target["cycle_id"] = selected_cycle
                configured_cycles.add(selected_cycle)
            active_targets.append(target)

        if len(configured_cycles) > 1:
            raise ValueError("一次单期运行不能混用多个 cycle_id")
        if not cycle and configured_cycles and any(
            not cycle_key(target.get("cycle_id")) for target in active_targets
        ):
            raise ValueError(
                "部分目标配置了 cycle_id、部分目标未配置；请用 --cycle-id 明确本轮统一周期"
            )

        lengths = dict(command_lengths)
        for target in active_targets:
            lengths = merge_cycle_lengths(lengths, target_cycle_lengths(target))
        if lengths:
            active_targets = [
                {**target, "cycle_lengths": dict(lengths)}
                for target in active_targets
            ]
    except ValueError as exc:
        parser.error(str(exc))

    for target in active_targets:
        if target.get('insecure_tls'):
            print(f"来源可信度降级：{target['name']} 显式关闭TLS证书验证")
    if not active_targets:
        parser.error("没有启用的抓取目标")
    run_id = args.run_id or uuid.uuid4().hex
    manifest_file = Path(args.manifest) if args.manifest else manifest_path_for_issue(issues[0])
    if not args.no_cache_update:
        atomic_write_json(CACHE_STATE_FILE, {'run_id': run_id, 'cache_updated': False, 'status': 'running'})
    print(f"正式输出目录：{RESULTS_DIR}")
    if not cycle:
        print("周期未指定：实时抓取照常进行；缓存观察值不能用于正式跨周期判重。")
    blocked_failures: list[CrawlFailure] = []
    blocked_urls: set[str] = set()
    crawl_target_items = [
        (index, target)
        for index, target in enumerate(active_targets)
        if target["url"] not in blocked_urls
    ]

    ordered_results: list[list[CrawlResult]] = [[] for _ in active_targets]
    ordered_failures: list[CrawlFailure | None] = [None for _ in active_targets]
    for index, target in enumerate(active_targets):
        if target["url"] in blocked_urls:
            ordered_failures[index] = next(
                failure for failure in blocked_failures if failure.url == target["url"]
            )
    max_workers = max(1, min(args.workers, len(crawl_target_items) or 1))

    print(f"并发线程数：{max_workers}")
    progress_started = time.monotonic()
    for done_count, failure in enumerate(blocked_failures, start=1):
        print(
            crawl_progress_line(
                done_count,
                len(active_targets),
                0,
                done_count,
                time.monotonic() - progress_started,
                failure.name,
            ),
            flush=True,
        )
        print(
            f"  失败[{failure_category(failure)}]：{failure.name} {failure.url}，原因：{failure.reason}",
            flush=True,
        )
    initial_results, initial_failures = crawl_targets(
        crawl_target_items,
        issues,
        max_workers,
        done_offset=len(blocked_failures),
        failure_offset=len(blocked_failures),
        total_override=len(active_targets),
    )
    for index in range(len(active_targets)):
        ordered_results[index] = initial_results.get(index, [])
        if index in initial_failures:
            ordered_failures[index] = initial_failures.get(index)

    initial_success = sum(1 for group in ordered_results if group)
    retry_rescued = 0
    retry_passes_used = 0
    retry_passes = max(0, args.retry_passes)
    for retry_pass in range(1, retry_passes + 1):
        retry_items = [
            (index, active_targets[index])
            for index, failure in enumerate(ordered_failures)
            if not ordered_results[index] and is_transient_failure(failure)
        ]
        if not retry_items:
            break

        print(f"\n网络失败补抓第 {retry_pass}/{retry_passes} 轮：{len(retry_items)} 条")
        retry_passes_used = retry_pass
        time.sleep(RETRY_PASS_WAIT)
        retry_results, retry_failures = crawl_targets(
            retry_items,
            issues,
            1,
            label="慢速单线程补抓：",
        )
        for index, _target in retry_items:
            found = retry_results.get(index, [])
            failure = retry_failures.get(index)
            if found and not failure:
                ordered_results[index] = found
                ordered_failures[index] = None
                retry_rescued += 1
            elif failure:
                ordered_failures[index] = failure

    results = dedupe_results([item for group in ordered_results for item in group])
    failures = [item for item in ordered_failures if item]

    result_file, failed_file, report_file = output_files_for_issues(issues)
    if not results and blocked_by_local_socket_policy(failures):
        print("\n本地网络权限阻止了全部访问，已保留原输出文件不覆盖。")
        print(f"失败原因：{failures[0].reason}")
        return 1

    stats = RunStats(
        total_targets=len(active_targets),
        initial_success=initial_success,
        retry_rescued=retry_rescued,
        retry_passes_used=retry_passes_used,
        workers=max_workers,
    )
    write_outputs(
        results,
        failures,
        result_file,
        failed_file,
        report_file,
        stats,
        skip_backup=skip_output_backup,
    )
    write_run_manifest(manifest_file, run_id, issues[0], results, failures,
                       active_targets, result_file, failed_file)
    cache_error = None
    cache_updated = False
    if args.no_cache_update or len(issues) > 1:
        reason = "按参数要求" if args.no_cache_update else "指定了多个期数"
        print(f"{CACHE_FILE.name}：本次{reason}不更新")
    else:
        successful_targets = sum(
            1
            for target_results, target_failure in zip(ordered_results, ordered_failures)
            if target_results and target_failure is None
        )
        total_targets = len(active_targets)
        success_rate = successful_targets * 100 / total_targets if total_targets else 0.0
        if not should_update_cache_for_success_rate(total_targets, successful_targets):
            print(
                f"{CACHE_FILE.name}：成功率 {success_rate:.2f}%（{successful_targets}/{total_targets}）"
                f"不超过 {CACHE_SUCCESS_RATE_THRESHOLD_PERCENT}%，缓存保持不变"
            )
        else:
            cache_error = persist_cache_after_realtime_result(
                CACHE_FILE,
                results,
                failures,
                issues,
                targets=active_targets, cycle_id=cycle, cycle_lengths=lengths,
            )
            cache_updated = cache_error is None
    if not args.no_cache_update:
        try:
            atomic_write_json(CACHE_STATE_FILE, {'run_id': run_id, 'cache_updated': cache_updated,
                              'status': 'finished', 'cache_error': cache_error})
        except OSError as exc:
            cache_error = cache_error or f"缓存同步状态写入失败：{exc}"
    if cache_error:
        print(f"{CACHE_FILE.name}：缓存更新未完成：{cache_error}")
    elif cache_updated:
        print(f"{CACHE_FILE.name}：已记录本次实时成功/失败状态")
    print(f"\n完成：成功 {len(results)} 条，失败 {len(failures)} 条")
    print(f"成功结果：{result_file}")
    if failures:
        print(f"失败记录：{failed_file}")
    else:
        print("失败记录：无失败，不生成失败文件")
    print(f"抓取报告：{report_file}")
    if failures:
        return 1
    if cache_error:
        return CACHE_UPDATE_FAILED_EXIT_CODE
    return 0


def main() -> int:
    try:
        with exclusive_run_lock(SCRIPT_DIR / ".crawler-and-duplicates.lock"):
            return _main_unlocked()
    except (RuntimeError, OSError) as exc:
        print(str(exc))
        return 2
    finally:
        from kill_numbers.acquisition.browser_pool import close_browser_pool
        close_browser_pool()


if __name__ == "__main__":
    raise SystemExit(main())
