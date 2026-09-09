import re

from kill_numbers.parsing.common import (
    CANDIDATE_REGION_WINDOW,
    resolve_candidate_window,
    candidate_rows,
    windowed_rows,
    extract_issue_numbers,
    filter_candidates_by_region,
    find_number_groups,
    has_duplicate_numbers,
    issue_position_window_starts,
    issue_segment_matches,
    iter_all_issue_segment_matches,
    needs_strict_region_window,
    normalize_region,
    anchor_scope_candidates,
    scope_text_by_anchor,
)
from kill_numbers.parsing.errors import AmbiguousSourceError, CandidateConflictError
from kill_numbers.text_utils import (
    html_to_text,
    normalize_issue,
    normalize_keyword,
    unique_keep_order,
)


def diagnose_issue_mismatch(
    text: str,
    issues: list[str],
    keywords: list[str] | None = None,
    expected_count: int | None = None,
    allow_duplicate_numbers: bool = False,
    region: str | None = None,
    anchor=None,
    stop_anchor=None,
    issue_position_window: int | None = None,
) -> dict[str, str]:
    diagnostics = {}
    text = html_to_text(text)
    text = scope_text_by_anchor(text, anchor, stop_anchor)
    keyword_list = [normalize_keyword(keyword) for keyword in (keywords or []) if keyword]
    normalized_region = normalize_region(region)
    allowed_window_starts = issue_position_window_starts(
        text,
        keywords,
        expected_count,
        region,
        issue_position_window,
        allow_duplicate_numbers,
    )

    for issue in issues:
        issue = normalize_issue(issue)
        messages = []
        region_candidates: list[tuple[list[str], str, int]] = []
        matched_keyword_without_group = False
        valid_candidate_outside_window = False
        for match in issue_segment_matches(text, issue):
            outside_window = (
                allowed_window_starts is not None
                and match.start() not in allowed_window_starts
            )
            segment = match.group(0)
            compact_segment = normalize_keyword(segment)
            if keyword_list and not any(keyword in compact_segment for keyword in keyword_list):
                if not outside_window:
                    messages.append("找到该期，但关键词不匹配")
                continue

            groups = find_number_groups(segment)
            if not groups:
                matched_keyword_without_group = True
            for group in groups:
                numbers = ",".join(group)
                if expected_count and len(group) != expected_count:
                    messages.append(
                        f"找到该期号码 {numbers}，实际 {len(group)} 个，配置要求 {expected_count} 个，已拒绝硬凑"
                    )
                    continue

                if not allow_duplicate_numbers and has_duplicate_numbers(group):
                    messages.append(f"找到该期号码 {numbers}，但号码有重复")
                    continue

                if outside_window:
                    valid_candidate_outside_window = True
                    continue
                region_candidates.append((group, segment, match.start()))

        if matched_keyword_without_group and not region_candidates:
            messages.append("找到该期和关键词，但没有识别到号码组")
        if valid_candidate_outside_window and not region_candidates:
            messages.append(
                f"找到该期候选，但不在配置位置 {normalized_region or '未配置'} "
                f"最新 {resolve_candidate_window(issue_position_window)} 条同栏目内"
            )
        if region_candidates:
            strict_region = needs_strict_region_window(region_candidates)
            if strict_region and not normalized_region:
                messages.append(
                    f"找到该期 {len(region_candidates)} 组候选，但配置缺少 top/bottom/顶部/尾部，已按严格规则拒绝"
                )
            selected = filter_candidates_by_region(
                region_candidates,
                normalized_region,
                strict_window=strict_region,
                require_region=strict_region,
            )
            if not selected:
                preview = " | ".join(",".join(group) for group, _segment, _start in region_candidates[:5])
                messages.append(
                    f"找到该期符合数量的候选，但不在配置位置 {normalized_region or '未配置'} 最新 {resolve_candidate_window(issue_position_window)} 组内：{preview}"
                )
        if messages:
            diagnostics[issue] = "；".join(unique_keep_order(messages))
    return diagnostics


def detect_available_issues(
    text: str,
    keywords: list[str] | None = None,
    expected_count: int | None = None,
    position: str = "first",
    allow_duplicate_numbers: bool = False,
    anchor=None,
    stop_anchor=None,
    region: str | None = None,
    issue_position_window: int | None = None,
    allowed_row_starts: set[int] | None = None,
    history_depth: int | None = None,
) -> list[str]:
    full_text = html_to_text(text)
    scopes = anchor_scope_candidates(full_text, anchor, stop_anchor)
    if history_depth is not None:
        history_depth = resolve_candidate_window(history_depth)

    scope_values = []
    for scope in scopes:
        candidates = list(
            candidate_rows(
                scope.text,
                keywords,
                expected_count,
                allow_duplicate_numbers,
            )
        )
        rows = (
            [row for row in candidates if row.start in allowed_row_starts]
            if allowed_row_starts is not None
            else windowed_rows(
                candidates,
                region,
                history_depth if history_depth is not None else issue_position_window,
            )
        )
        available = []
        for row in rows:
            if len(row.groups) != 1:
                raise CandidateConflictError(f"{row.issue}期 候选不唯一")
            if row.issue not in available:
                available.append(row.issue)
        if available:
            scope_values.append((scope, available))

    if not scope_values:
        return []
    signatures = {tuple(values) for _scope, values in scope_values}
    if len(signatures) > 1:
        raise AmbiguousSourceError(
            "正文锚点对应多个不同历史区块，无法确定唯一期数顺序"
        )
    return min(
        scope_values,
        key=lambda item: (item[0].end - item[0].start, item[0].start),
    )[1]


def nearest_issues(available: list[str], wanted: list[str], limit: int = 20) -> list[str]:
    wanted_nums = [int(issue) for issue in wanted]
    return sorted(
        unique_keep_order(available),
        key=lambda issue: (
            min(abs(int(issue) - wanted_num) for wanted_num in wanted_nums),
            -int(issue),
        ),
    )[:limit]
