import re
import hashlib
from collections.abc import Iterable, Mapping

from kill_numbers.domain.models import (
    CandidateEvidence,
    CrawlFailure,
    CrawlResult,
    SourceDocument,
)
from kill_numbers.parsing.common import (
    find_number_groups,
    candidate_rows,
    windowed_rows,
    resolve_candidate_window,
    target_candidate_window,
    normalize_region,
    has_duplicate_numbers,
    issue_segment_matches,
    scope_text_by_anchor_with_offset,
    valid_number,
)
from kill_numbers.parsing.dedicated.site_parsers import normalize_identity_article_current_placeholder
from kill_numbers.text_utils import as_list, html_to_text, normalize_issue, unique_keep_order
from kill_numbers.parsing.source_scope import target_for_document
from kill_numbers.parsing.evidence_scope import evidence_section


def _evidence_window_size(target: Mapping) -> int:
    if target.get("_history_discovery") is True:
        return resolve_candidate_window(target.get("_history_depth"))
    return target_candidate_window(target)


def _candidate_proof(target, issue, numbers, document):
    from kill_numbers.parsing.registry import parse_target_content
    if document.fingerprint != hashlib.sha256(document.content.encode("utf-8", errors="replace")).hexdigest():
        raise ValueError("来源文档内容与指纹不匹配")
    effective = target_for_document(target, document)
    parser = str(effective.get("special_parser") or "")
    # These acquisition adapters have already selected a unique article. Replay
    # its actual body parser, not the listing-page parser.
    if parser == "zuojianzifu_link_chain":
        effective["special_parser"] = ""
    elif parser == "ttss_paginated_identity_top_10":
        effective["special_parser"] = "identity_article_top_10"
    if effective.get("region") or effective.get("special_parser"):
        found = parse_target_content(document.content, effective, [issue])
        if found.get(issue) != numbers:
            raise ValueError(f"{issue}期来源证据与专属解析/方向窗口不匹配")
    text, section, offset = evidence_section(
        document.content, effective, issue=issue, numbers=numbers
    )
    keywords = [] if parser == "macau_baoma" else effective.get("keywords")
    if effective.get("special_parser") == "identity_article_top_10":
        keywords = [effective["article_identity"]]
    rows = list(candidate_rows(section, keywords, effective.get("count"),
                               effective.get("allow_duplicate_numbers", False)))
    allowed = effective.get("_allowed_row_starts")
    selected = ([row for row in rows if row.start in allowed] if allowed is not None
                else windowed_rows(rows, effective.get("region"), _evidence_window_size(effective)))
    locations = [(rank, row.start) for rank, row in enumerate(selected)
                 if row.issue == issue and tuple(numbers) in row.groups]
    if not locations and not effective.get("region") and not effective.get("special_parser"):
        # Small-count unscoped helper contracts (not a formal site parser).
        for match in issue_segment_matches(section, issue):
            groups = re.findall(r"(?<!\d)\d+(?:[\s.,，。、;；|/\\]+\d+)+(?!\d)", match.group(0))
            for group in groups:
                tokens = re.findall(r"\d+", group)
                if all(len(n) <= 2 and valid_number(n) for n in tokens) and [f"{int(n):02d}" for n in tokens] == numbers:
                    locations.append((0, match.start()))
    if not locations:
        raise ValueError(f"{issue}期候选缺少同块栏目/偏移证据")
    rank, location = locations[-1] if normalize_region(effective.get("region")) == "bottom" else locations[0]
    if allowed is not None:
        rank = document.metadata["row_ranks"][location]
    anchor = str(effective.get("anchor") or "")
    return effective, anchor, offset, offset + len(section), offset + location, rank


def _candidate_location(target, issue, numbers, document):
    _, anchor, start, end, position, _rank = _candidate_proof(target, issue, numbers, document)
    return anchor, start, end, position


def _normalize_numbers(
    raw_numbers: Iterable[object],
    *,
    issue: str,
) -> tuple[list[str], list[str]]:
    numbers = []
    errors = []
    for raw_number in raw_numbers:
        text = str(raw_number).strip()
        if not re.fullmatch(r"\d{1,2}", text):
            errors.append(f"{issue}期号码格式无效：{text}")
            continue
        number = f"{int(text):02d}"
        if not valid_number(number):
            errors.append(f"{issue}期包含超出01至49的号码")
            continue
        numbers.append(number)
    return numbers, errors


def _validate_source_document(
    target: Mapping,
    document: SourceDocument | None,
) -> list[str]:
    if document is None:
        return []
    errors = []
    if not document.content.strip() or not document.fingerprint:
        errors.append("来源文档证据不完整")
    if not document.metadata.get("parseable", True):
        errors.append("来源文档不可解析")
    source_pattern = str(target.get("source_url_pattern") or "").strip()
    if source_pattern and not re.search(source_pattern, document.url, re.I):
        errors.append("来源文档不符合专属 URL 契约")
    return errors


def evidence_from_source_document(
    target: Mapping,
    issue: str,
    numbers: Iterable[object],
    document: SourceDocument,
) -> CandidateEvidence:
    """Keep the source identity with a successful result without retaining page text."""
    normalized_issue = normalize_issue(issue)
    normalized_numbers, errors = _normalize_numbers(numbers, issue=normalized_issue)
    if errors:
        raise ValueError("；".join(errors))
    if not document.content.strip() or not document.fingerprint:
        raise ValueError(f"{normalized_issue}期来源文档证据不完整")
    effective, anchor, section_start, section_end, candidate_start, rank = _candidate_proof(
        target,
        normalized_issue,
        normalized_numbers,
        document,
    )
    return CandidateEvidence(
        issue=normalized_issue,
        numbers=tuple(normalized_numbers),
        source_kind=document.kind,
        source_url=document.url,
        document_fingerprint=document.fingerprint,
        anchor=anchor,
        section_start=section_start,
        section_end=section_end,
        candidate_start=candidate_start,
        scope_kind=effective.get("_scope_kind", "dedicated_parser" if effective.get("special_parser") else "text_anchor"),
        source_identity=effective.get("_source_identity", ""),
        region=normalize_region(target.get("region")),
        window_size=_evidence_window_size(target),
        row_rank=rank,
        parser_id=str(target.get("special_parser") or "generic"),
    )


def _validate_candidate_evidence(
    target: Mapping,
    issue: str,
    numbers: list[str],
    evidence: CandidateEvidence | None,
) -> list[str]:
    if evidence is None:
        return [f"{issue}期来源证据缺失"]

    errors = []
    if normalize_issue(evidence.issue) != issue:
        errors.append(f"{issue}期来源证据期数不匹配")
    if list(evidence.numbers) != numbers:
        errors.append(f"{issue}期来源证据号码不匹配")
    if not evidence.source_kind or not evidence.source_url or not evidence.document_fingerprint:
        errors.append(f"{issue}期来源证据不完整")
    if (
        evidence.section_start < 0
        or evidence.section_end <= evidence.section_start
        or evidence.candidate_start < evidence.section_start
        or evidence.candidate_start >= evidence.section_end
    ):
        errors.append(f"{issue}期来源证据缺少同块栏目/偏移")
    expected_region = normalize_region(target.get("region"))
    if expected_region and (
        evidence.region != expected_region
        or evidence.window_size != _evidence_window_size(target)
        or not 0 <= evidence.row_rank < evidence.window_size
        or evidence.parser_id != str(target.get("special_parser") or "generic")
    ):
        errors.append(f"{issue}期来源证据方向/窗口不匹配")
    if evidence.scope_kind == "source_identity":
        from kill_numbers.text_utils import normalize_keyword
        if not evidence.source_identity or normalize_keyword(evidence.source_identity) != normalize_keyword(str(target.get("anchor") or "")):
            errors.append(f"{issue}期来源证据身份不匹配")
    elif target.get("anchor") and not evidence.anchor and not target.get("special_parser"):
        errors.append(f"{issue}期来源证据锚点缺失")
    source_pattern = str(target.get("source_url_pattern") or "").strip()
    if source_pattern and not re.search(source_pattern, evidence.source_url, re.I):
        errors.append(f"{issue}期来源证据不符合专属 URL 契约")
    return errors


def validate_issue_map(
    target: Mapping,
    requested_issues: Iterable[str],
    issue_map: Mapping[str, Iterable[object]],
    *,
    source_document: SourceDocument | None = None,
    source_documents: Mapping[str, SourceDocument] | None = None,
    require_source_document: bool = False,
) -> dict[str, list[str]]:
    requested = [
        normalize_issue(issue)
        for issue in requested_issues
        if normalize_issue(issue)
    ]
    requested_set = set(requested)
    errors = []
    documents_by_issue = {
        normalize_issue(issue): document
        for issue, document in (source_documents or {}).items()
        if normalize_issue(issue) and isinstance(document, SourceDocument)
    }
    candidates: dict[str, list[list[str]]] = {}
    expected_count = target.get("count")
    allow_duplicates = bool(target.get("allow_duplicate_numbers", False))

    for raw_issue, raw_numbers in issue_map.items():
        issue = normalize_issue(raw_issue)
        numbers, number_errors = _normalize_numbers(raw_numbers, issue=issue)
        errors.extend(number_errors)
        document = documents_by_issue.get(issue, source_document)
        if document is None:
            if require_source_document:
                errors.append(f"{issue or raw_issue}期来源文档证据缺失")
        else:
            errors.extend(_validate_source_document(target, document))
        if issue not in requested_set:
            errors.append(f"混入非指定期数 {issue or raw_issue}期")
        if not numbers:
            errors.append(f"{issue or raw_issue}期号码为空")
        if expected_count and len(numbers) != expected_count:
            errors.append(
                f"{issue or raw_issue}期号码数量 {len(numbers)}，配置要求 {expected_count}"
            )
        if not allow_duplicates and has_duplicate_numbers(numbers):
            errors.append(f"{issue or raw_issue}期号码有重复")
        if number_errors or not numbers:
            continue
        distinct = candidates.setdefault(issue, [])
        if numbers not in distinct:
            distinct.append(numbers)

    accepted: dict[str, list[str]] = {}
    for issue in requested:
        distinct = candidates.get(issue, [])
        if len(distinct) > 1:
            errors.append(f"{issue}期同期候选冲突")
        elif distinct:
            accepted[issue] = distinct[0]
        else:
            errors.append(f"缺少指定期数 {issue}期")

    if errors:
        raise ValueError("；".join(unique_keep_order(errors)))
    return accepted


def validate_crawl_results(
    target: Mapping,
    requested_issues: Iterable[str],
    results: Iterable[CrawlResult],
    failure: CrawlFailure | None = None,
) -> tuple[list[CrawlResult], str]:
    results = list(results)
    errors = []
    if failure is not None:
        errors.append(failure.reason.strip() or "runner 返回失败但未提供原因")

    expected_name = str(target.get("name") or target.get("url") or "")
    expected_url = str(target.get("url") or "")
    issue_candidates: dict[str, list[list[str]]] = {}
    for result in results:
        if result.name != expected_name or result.url != expected_url:
            errors.append(f"返回站点身份不匹配：{result.name} {result.url}")
        issue = normalize_issue(result.issue)
        numbers, number_errors = _normalize_numbers(result.numbers, issue=issue)
        errors.extend(number_errors)
        errors.extend(
            _validate_candidate_evidence(target, issue, numbers, result.evidence)
        )
        issue_candidates.setdefault(issue, []).append(numbers)

    flattened: dict[str, list[str]] = {}
    for issue, candidates in issue_candidates.items():
        distinct = []
        for candidate in candidates:
            if candidate not in distinct:
                distinct.append(candidate)
        if len(distinct) > 1:
            errors.append(f"{issue}期同期候选冲突")
        elif distinct:
            flattened[issue] = distinct[0]

    try:
        accepted_map = validate_issue_map(target, requested_issues, flattened)
    except ValueError as exc:
        errors.append(str(exc))
        accepted_map = {}

    if errors:
        return [], "；".join(unique_keep_order(errors))

    accepted = [
        CrawlResult(
            url=expected_url,
            name=expected_name,
            issue=issue,
            numbers=numbers,
            evidence=next(
                (
                    result.evidence
                    for result in results
                    if normalize_issue(result.issue) == issue
                    and _normalize_numbers(result.numbers, issue=issue)[0] == numbers
                ),
                None,
            ),
        )
        for issue, numbers in accepted_map.items()
    ]
    return accepted, "；".join(unique_keep_order(errors))
