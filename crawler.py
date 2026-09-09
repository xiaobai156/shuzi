import argparse
import os
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
    resolve_candidate_window,
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
from kill_numbers.infrastructure.file_store import atomic_write_text
from kill_numbers.infrastructure.output_repository import (
    backup_existing_outputs as backup_outputs_storage,
    cleanup_old_backups as cleanup_old_backups_storage,
    output_files_for_issues as output_files_for_issues_storage,
    remove_stale_file,
)
from kill_numbers.infrastructure.target_repository import read_target_data, write_target_data


# 默认期数：你也可以运行时用 --issues 119 或 --issues 119,120 指定。
DEFAULT_ISSUES = "128"
DEFAULT_WORKERS = 8
DEFAULT_RETRY_PASSES = 2
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
RESULTS_DIR = Path(
    os.environ.get(
        "SHUZI_RESULTS_DIR",
        r"C:\Users\Administrator\Desktop\每天工具\爬虫合集\大围杀号生肖数据统一归纳",
    )
).expanduser()


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
# name 留空会自动取：用户页昵称 / 作者 / 标题里的名称。
def load_targets(path: Path = TARGETS_FILE) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"目标配置文件不存在：{path}")
    data = read_target_data(path)
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict) or not item.get("url"):
            raise ValueError(f"targets.json 第 {index} 条缺少 url")
        special_parser = item.get("special_parser")
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
        count = item.get("count")
        if count is not None and (not isinstance(count, int) or isinstance(count, bool) or count <= 0):
            raise ValueError(f"targets.json 第 {index} 条 count 必须是正整数")
        issue_window = item.get("issue_position_window")
        if issue_window is not None and (
            isinstance(issue_window, bool)
            or not isinstance(issue_window, int)
            or issue_window <= 0
        ):
            raise ValueError(
                f"targets.json 第 {index} 条 issue_position_window 必须是正整数"
            )
        position = item.get("position", "first")
        if position not in {"first", "last"}:
            raise ValueError(f"targets.json 第 {index} 条 position 只能是 first/last")
        for boolean_field in (
            "allow_ambiguous",
            "allow_duplicate_numbers",
            "disabled",
            "browser_fallback",
        ):
            if boolean_field in item and not isinstance(item[boolean_field], bool):
                raise ValueError(
                    f"targets.json 第 {index} 条 {boolean_field} 必须是布尔值"
                )
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
    return [item for item in data if not item.get("disabled")]


def save_targets(targets: list[dict], path: Path = TARGETS_FILE) -> None:
    write_target_data(path, targets)


TARGETS = load_targets()


















































































































def crawl_qiancai_liangde_page(target: dict) -> tuple[str, str]:
    encoding = str(target.get("encoding") or "")
    if encoding != "gb18030":
        raise ValueError("钱彩两得专属页面编码必须是 gb18030")
    content = fetch_encoded_page(str(target["url"]), encoding)
    return str(target.get("article_identity") or target.get("name") or "钱彩两得"), content






























def crawl_zuojianzifu_link_chain(
    target: dict,
    issues: list[str],
) -> tuple[str, str, dict[str, list[str]], dict[str, SourceDocument]]:
    if not issues:
        raise ValueError("没有指定期数")

    def exact_link_from_documents(
        documents: list[SourceDocument],
        issue: str,
        *keywords: str,
    ) -> tuple[str, str]:
        parseable = parseable_documents(documents)
        priorities = sorted(
            {document.priority for document in parseable},
            reverse=True,
        )
        for priority in priorities:
            matches = []
            for document in (
                item for item in parseable if item.priority == priority
            ):
                try:
                    link = exact_issue_link(
                        topic_links(document.content),
                        issue,
                        *keywords,
                    )
                except ValueError:
                    continue
                if link not in matches:
                    matches.append(link)
            if len(matches) > 1:
                raise ValueError(
                    f"{normalize_issue(issue)}期专属链接候选冲突"
                )
            if matches:
                return matches[0]
        raise ValueError(f"{normalize_issue(issue)}期专属链接不唯一或不存在")

    _listing_name, listing_documents = discover_static_documents(target["url"])
    article_links = [
        (
            normalize_issue(issue),
            exact_link_from_documents(
                listing_documents,
                issue,
                "作茧自缚",
            )[0],
        )
        for issue in issues
    ]
    article_names = []
    article_documents: list[SourceDocument] = []
    article_documents_by_issue: dict[str, SourceDocument] = {}
    issue_map: dict[str, list[str]] = {}
    for normalized_issue, article_href in article_links:
        article_url = urljoin(target["url"], article_href)
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

    requested_issues = unique_keep_order(normalize_issue(issue) for issue in issues)
    page_limit = target.get("pagination_limit")
    if isinstance(page_limit, bool) or not isinstance(page_limit, int) or page_limit <= 0:
        raise ValueError(f"分页上限无效：{page_limit}")

    next_text = str(target.get("pagination_next_text") or "").strip()
    link_keywords = [
        normalize_keyword(str(keyword).strip())
        for keyword in (target.get("link_keywords") or [])
        if str(keyword).strip()
    ]
    if not next_text or not link_keywords:
        raise ValueError("分页身份文章专属采集缺少 next_text 或 link_keywords")

    current_url = remove_fragment(str(target["url"]))
    visited_urls: set[str] = set()
    issue_link_candidates: dict[str, list[str]] = {
        issue: [] for issue in requested_issues
    }

    for page_number in range(1, page_limit + 1):
        if current_url in visited_urls:
            raise ValueError(f"分页链接循环：{current_url}")
        visited_urls.add(current_url)

        _listing_name, listing_documents = discover_static_documents(current_url)
        parseable = parseable_documents(listing_documents)
        if not parseable:
            raise ValueError(f"第{page_number}页没有可解析的列表文档")

        next_candidates: list[str] = []
        for document in parseable:
            links = topic_links(document.content)
            for requested_issue in requested_issues:
                issue_pattern = re.compile(
                    rf"(?<!\d)0?{re.escape(requested_issue)}\s*期(?!\d)"
                )
                matches = [
                    href
                    for href, label in links
                    if issue_pattern.search(label)
                    and all(keyword in normalize_keyword(label) for keyword in link_keywords)
                ]
                distinct_matches = list(dict.fromkeys(matches))
                if len(distinct_matches) > 1:
                    raise ValueError(
                        f"{requested_issue}期专属链接候选冲突：{distinct_matches}"
                    )
                issue_link_candidates[requested_issue].extend(distinct_matches)

            next_candidates.extend(
                href
                for href, label in links
                if label.strip() == next_text
            )

        distinct_next = list(dict.fromkeys(next_candidates))
        if len(distinct_next) > 1:
            raise ValueError(f"第{page_number}页分页下一页链接冲突：{distinct_next}")
        if not distinct_next:
            break
        next_url = urljoin(current_url, distinct_next[0])
        if not distinct_next[0].strip() or distinct_next[0].strip() == "#":
            break
        if remove_fragment(next_url) == current_url:
            break
        if page_number == page_limit:
            raise ValueError(f"分页超过配置上限 {page_limit}")
        current_url = remove_fragment(next_url)
    else:
        raise ValueError(f"分页未在配置上限 {page_limit} 内结束")

    article_links: dict[str, str] = {}
    for requested_issue in requested_issues:
        distinct_links = list(dict.fromkeys(issue_link_candidates[requested_issue]))
        if len(distinct_links) > 1:
            raise ValueError(
                f"{requested_issue}期专属链接候选冲突：{distinct_links}"
            )
        if not distinct_links:
            raise ValueError(
                f"{requested_issue}期专属链接不唯一或不存在：{distinct_links}"
            )
        article_links[requested_issue] = distinct_links[0]

    article_names: list[str] = []
    article_documents: list[SourceDocument] = []
    article_documents_by_issue: dict[str, SourceDocument] = {}
    issue_map: dict[str, list[str]] = {}
    documents_by_url: dict[str, tuple[str, list[SourceDocument]]] = {}
    for requested_issue in requested_issues:
        article_url = urljoin(str(target["url"]), article_links[requested_issue])
        if article_url not in documents_by_url:
            documents_by_url[article_url] = discover_static_documents(article_url)
        article_name, documents = documents_by_url[article_url]
        article_target = dict(target)
        article_target["special_parser"] = "identity_article_top_10"
        found, selected_document = parse_target_documents(
            documents,
            article_target,
            [requested_issue],
        )
        if requested_issue not in found:
            raise ValueError(
                f"{requested_issue}期专属文章没有找到符合配置的顶部号码"
            )
        if selected_document is None:
            raise ValueError(f"{requested_issue}期专属文章缺少来源文档证据")
        issue_map[requested_issue] = found[requested_issue]
        article_names.append(article_name)
        article_documents.append(selected_document)
        article_documents_by_issue[requested_issue] = selected_document

    return (
        article_names[0],
        document_debug_text(article_documents),
        issue_map,
        article_documents_by_issue,
    )


def admin_content_matches_target(content: str, target: dict | None, issues: list[str] | None) -> bool:
    if not target or not issues:
        return bool(content.strip())
    if target.get("special_parser") == "macau_baoma":
        found = extract_macau_baoma_numbers(
            content,
            issues,
            expected_count=target.get("count"),
            region=target.get("region"),
        )
        return bool(found)
    if target.get("special_parser") == "identity_article_bottom_10":
        try:
            return bool(identity_article_bottom_10_candidates(content, target))
        except Exception:
            return False
    return strict_target_extracts(content, target, issues)


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
                except Exception:
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
    return ValueError(
        f"{issue}期 跨文档候选冲突，已停止输出避免抓错：" + " | ".join(previews)
    )


def _directional_parseable_documents(
    documents: list[SourceDocument],
    target: dict,
) -> list[SourceDocument]:
    """Apply top/bottom to document sequences such as user forum topics."""
    parseable = parseable_documents(documents)
    groups: dict[str, list[SourceDocument]] = {}
    for document in parseable:
        sequence = document.metadata.get("region_sequence")
        index = document.metadata.get("region_index")
        if sequence is None or not isinstance(index, int):
            continue
        groups.setdefault(str(sequence), []).append(document)

    if not groups:
        return parseable

    region = normalize_region(target.get("region"))
    window = resolve_candidate_window(target.get("issue_position_window"))
    selected_ids: set[int] = set()
    for group in groups.values():
        ordered = sorted(
            group,
            key=lambda document: int(document.metadata["region_index"]),
        )
        selected = (
            ordered[:window]
            if region == "top"
            else ordered[-window:]
            if region == "bottom"
            else ordered
        )
        selected_ids.update(id(document) for document in selected)

    return [
        document
        for document in parseable
        if not document.metadata.get("region_sequence")
        or id(document) in selected_ids
    ]


def parse_target_documents(
    documents: list[SourceDocument],
    target: dict,
    issues: list[str],
) -> tuple[dict[str, list[str]], SourceDocument | None]:
    parseable = parseable_documents(documents)
    source_url_pattern = str(target.get("source_url_pattern") or "").strip()
    if source_url_pattern:
        matching_documents = [
            document
            for document in parseable
            if re.search(source_url_pattern, document.url, re.I)
        ]
        if not matching_documents:
            raise ValueError(
                f"没有找到专属来源文档：{source_url_pattern}"
            )
        parseable = matching_documents
        source_anchor = str(target.get("source_anchor") or "").strip()
        if source_anchor:
            target = dict(target)
            target["anchor"] = source_anchor
    parseable = _directional_parseable_documents(parseable, target)
    priorities = sorted({document.priority for document in parseable}, reverse=True)
    hard_markers = (
        "候选不唯一",
        "候选冲突",
        "号码有重复",
        "跨周期候选",
        "同时存在数字期号",
    )

    all_tier_results: list[tuple[SourceDocument, dict[str, list[str]]]] = []
    hard_errors: list[Exception] = []
    for priority in priorities:
        tier_results: list[tuple[SourceDocument, dict[str, list[str]]]] = []
        for document in (
            item for item in parseable if item.priority == priority
        ):
            document_target = target
            if (
                document.identity
                and normalize_keyword(document.identity)
                == normalize_keyword(str(target.get("anchor") or ""))
            ):
                document_target = dict(target)
                document_target["anchor"] = ""
            try:
                issue_map = parse_target_content(
                    document.content,
                    document_target,
                    issues,
                )
            except Exception as exc:
                if any(marker in str(exc) for marker in hard_markers):
                    hard_errors.append(exc)
                continue
            if issue_map:
                tier_results.append((document, issue_map))
        all_tier_results.extend(tier_results)

    if hard_errors:
        raise hard_errors[0]
    if not all_tier_results:
        return {}, None

    for issue in issues:
        normalized = normalize_issue(issue)
        candidates = [
            (document, issue_map[normalized])
            for document, issue_map in all_tier_results
            if normalized in issue_map
        ]
        unique_values = {tuple(numbers) for _document, numbers in candidates}
        if len(unique_values) > 1:
            raise _document_conflict_error(normalized, candidates)

    selected_document, selected_map = max(
        all_tier_results,
        key=lambda item: (
            len(item[1]),
            item[0].priority,
            -parseable.index(item[0]),
        ),
    )
    return selected_map, selected_document


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

    name, documents = discover_static_documents(url)
    if (
        target.get("browser_fallback") is True
        or contract_is_split_across_documents(documents, target)
    ):
        try:
            documents.extend(render_page_documents(url))
        except Exception:
            pass
    return name, documents


def available_issues_for_documents(
    documents: list[SourceDocument],
    target: dict,
) -> tuple[list[str], SourceDocument | None]:
    parseable = parseable_documents(documents)
    source_url_pattern = str(target.get("source_url_pattern") or "").strip()
    if source_url_pattern:
        parseable = [
            document
            for document in parseable
            if re.search(source_url_pattern, document.url, re.I)
        ]
    parseable = _directional_parseable_documents(parseable, target)
    priorities = sorted({document.priority for document in parseable}, reverse=True)
    for priority in priorities:
        candidates = []
        for document in (item for item in parseable if item.priority == priority):
            document_target = target
            source_anchor = str(target.get("source_anchor") or "").strip()
            if source_anchor:
                document_target = dict(target)
                document_target["anchor"] = source_anchor
            elif (
                document.identity
                and normalize_keyword(document.identity)
                == normalize_keyword(str(target.get("anchor") or ""))
            ):
                document_target = dict(target)
                document_target["anchor"] = ""
            try:
                available = available_issues_from_content(
                    document.content,
                    document_target,
                )
            except Exception:
                continue
            if available:
                candidates.append((available, document))
        if candidates:
            return max(candidates, key=lambda item: len(item[0]))
    return [], None


def _crawl_dependencies() -> CrawlDependencies:
    return CrawlDependencies(
        fetch_target_documents=fetch_target_documents,
        parse_target_documents=parse_target_documents,
        crawl_zuojianzifu_link_chain=crawl_zuojianzifu_link_chain,
        contract_is_split_across_documents=contract_is_split_across_documents,
        save_debug_page=save_debug_page,
        crawl_ttss_paginated_identity_top_10=crawl_ttss_paginated_identity_top_10,
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
    except Exception:
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
) -> None:
    update_recent_cache_storage(
        cache_path,
        results,
        issues,
        recent_count,
        failures=failures,
    )


def persist_cache_after_realtime_result(
    cache_path: Path,
    results: list[CrawlResult],
    failures: list[CrawlFailure],
    issues: list[str],
) -> str | None:
    """Persist cache after realtime outputs are finalized without changing them."""
    try:
        update_recent_duplicate_cache(
            cache_path,
            results,
            issues,
            recent_count=10,
            failures=failures,
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
        help="指定期数，多个用逗号分隔，例如：119 或 119,120",
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
        help="网络失败后的慢速补抓轮数，默认 2；设为 0 可关闭",
    )
    parser.add_argument(
        "--no-cache-update",
        action="store_true",
        help="不更新 recent_10_cache.json；多期独立抓取专用",
    )
    args = parser.parse_args()
    skip_output_backup = "--issues" in sys.argv[1:]
    issues = parse_issues(args.issues)
    if not issues:
        print("请指定期数，例如：python crawler.py --issues 119")
        return 2
    if len(issues) != 1:
        print("正式 crawler.py 每次只允许一个期数；多个期数请使用多期入口逐期执行。")
        return 2

    print(f"正式输出目录：{RESULTS_DIR}")

    # Risk checks belong to the formal target pipeline.  This entrypoint must
    # not fetch risky sites twice or mutate targets.json during a run.
    active_targets = TARGETS
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
            )
            cache_updated = cache_error is None
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
    except RuntimeError as exc:
        print(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
