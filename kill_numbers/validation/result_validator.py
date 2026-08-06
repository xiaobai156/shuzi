import re
from collections.abc import Iterable, Mapping

from kill_numbers.domain.models import (
    CandidateEvidence,
    CrawlFailure,
    CrawlResult,
    SourceDocument,
)
from kill_numbers.parsing.common import (
    find_number_groups,
    has_duplicate_numbers,
    issue_segment_matches,
    scope_text_by_anchor_with_offset,
    valid_number,
)
from kill_numbers.parsing.dedicated.site_parsers import normalize_identity_article_current_placeholder
from kill_numbers.text_utils import as_list, html_to_text, normalize_issue, unique_keep_order


def _candidate_location(
    target: Mapping,
    issue: str,
    numbers: list[str],
    document: SourceDocument,
) -> tuple[str, int, int, int]:
    text = html_to_text(document.content)
    if target.get("special_parser") == "identity_article_bottom_10":
        text = normalize_identity_article_current_placeholder(text, target)

    anchors = [
        str(value).strip()
        for value in (
            *as_list(target.get("anchor")),
            *as_list(target.get("source_anchor")),
            target.get("article_identity"),
        )
        if str(value).strip()
    ]
    region = str(target.get("region") or "").lower()

    def evidence_groups(segment: str) -> list[list[str]]:
        groups = find_number_groups(segment)
        if groups:
            return groups
        expected_count = target.get("count")
        if not isinstance(expected_count, int) or expected_count <= 0:
            return []
        pattern = re.compile(
            rf"(?<!\d)\d{{1,2}}(?:[\s.,，。、;；|/\\]+\d{{1,2}}){{{expected_count - 1}}}"
        )
        return [
            [f"{int(number):02d}" for number in re.findall(r"\d{1,2}", match.group(0))]
            for match in pattern.finditer(segment)
        ]

    for anchor in (*unique_keep_order(anchors), ""):
        try:
            section, section_start = scope_text_by_anchor_with_offset(
                text,
                anchor,
                target.get("stop_anchor"),
            )
        except ValueError:
            continue

        locations = []
        for match in issue_segment_matches(section, issue):
            for group in evidence_groups(match.group(0)):
                normalized = [f"{int(number):02d}" for number in group]
                if normalized == numbers:
                    locations.append(section_start + match.start())
        if locations:
            candidate_start = locations[-1] if region in {"bottom", "下", "尾部", "底部"} else locations[0]
            return (
                anchor,
                section_start,
                section_start + len(section),
                candidate_start,
            )

    raise ValueError(f"{issue}期候选缺少同块栏目/偏移证据")


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
    anchor, section_start, section_end, candidate_start = _candidate_location(
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
                    and list(result.numbers) == numbers
                ),
                None,
            ),
        )
        for issue, numbers in accepted_map.items()
    ]
    return accepted, "；".join(unique_keep_order(errors))
