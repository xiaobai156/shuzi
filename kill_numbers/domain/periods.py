"""Explicit cycle identity. Neither the wall clock nor a smaller issue proves a rollover."""
import re
from urllib.parse import urlsplit, urlunsplit
from kill_numbers.text_utils import normalize_issue


def cycle_key(value) -> str:
    if value in (None, ''):
        return ''
    text = str(value).strip()
    if not re.fullmatch(r'[1-9][0-9]{0,5}', text):
        raise ValueError('cycle_id 必须是明确的正整数周期标识（例如 2026）')
    return str(int(text))


def canonical_url(value: str) -> str:
    parsed = urlsplit(str(value).strip())
    # Paths, query values and hash-router identities are case sensitive.
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path,
                       parsed.query, parsed.fragment))


def period_key(cycle, issue):
    return f'{cycle_key(cycle)}:{normalize_issue(issue)}'


def split_period(value):
    text = str(value).replace('期', '').strip()
    if ':' in text:
        cycle, issue = text.split(':', 1)
        return cycle_key(cycle), normalize_issue(issue)
    return '', normalize_issue(text)


def period_sort_key(value):
    cycle, issue = split_period(value)
    return int(cycle or 0), int(issue)


def validate_cycle_lengths(lengths):
    if not isinstance(lengths, dict):
        raise ValueError('cycle_lengths 必须是周期到期数上限的字典')
    result = {}
    for cycle, count in lengths.items():
        if type(count) is not int or not 1 <= count <= 999:
            raise ValueError('周期期数上限必须是 1 至 999 的整数')
        normalized = cycle_key(cycle)
        if not normalized:
            raise ValueError('周期期数上限缺少cycle_id')
        result[normalized] = count
    return result




def merge_cycle_lengths(*sources):
    """Merge explicit cycle limits and reject contradictory declarations."""
    merged = {}
    for source in sources:
        for cycle, count in validate_cycle_lengths(source or {}).items():
            if cycle in merged and merged[cycle] != count:
                raise ValueError(
                    f"周期 {cycle} 的期数上限冲突：{merged[cycle]} 与 {count}"
                )
            merged[cycle] = count
    return merged

def previous_period(value, lengths=None):
    cycle, issue = split_period(value)
    if int(issue) > 1:
        return period_key(cycle, int(issue)-1)
    if not cycle or str(int(cycle)-1) not in (lengths or {}):
        raise ValueError('跨周期连续性未确认：缺少上一周期的明确期数上限')
    previous_cycle = str(int(cycle)-1)
    return period_key(previous_cycle, lengths[previous_cycle])


def recent_periods(latest, count, lengths=None):
    result = [latest]
    for _ in range(count-1):
        result.append(previous_period(result[-1], lengths))
    return list(reversed(result))


def are_consecutive(left, right, lengths=None):
    try:
        return previous_period(right, lengths) == period_key(*split_period(left))
    except ValueError:
        return False


def target_cycle_lengths(target):
    """Return explicitly configured cycle lengths, including CLI shorthand."""
    lengths = validate_cycle_lengths(target.get('cycle_lengths', {}))
    current_cycle = cycle_key(target.get('cycle_id'))
    previous_length = target.get('previous_cycle_length')
    if previous_length is not None:
        if not current_cycle or int(current_cycle) <= 1:
            raise ValueError('previous_cycle_length 需要明确的当前 cycle_id')
        if type(previous_length) is not int or not 1 <= previous_length <= 999:
            raise ValueError('previous_cycle_length 必须是 1 至 999 的整数')
        previous_cycle = str(int(current_cycle) - 1)
        if previous_cycle in lengths and lengths[previous_cycle] != previous_length:
            raise ValueError('上一周期期数上限与 cycle_lengths 冲突')
        lengths[previous_cycle] = previous_length
    return lengths


def previous_issue_for_target(issue, target):
    """Return the visible previous issue without inventing a rollover length."""
    current = int(normalize_issue(issue))
    if current > 1:
        return str(current - 1)
    cycle = cycle_key(target.get('cycle_id'))
    lengths = target_cycle_lengths(target)
    if not cycle or int(cycle) <= 1:
        raise ValueError('当前为1期，但缺少明确 cycle_id 和上一周期长度')
    previous_cycle = str(int(cycle) - 1)
    if previous_cycle not in lengths:
        raise ValueError('当前为1期，但缺少上一周期的明确期数上限')
    return str(lengths[previous_cycle])


def rollover_seam_indices(issues, target=None):
    """Locate document-observed rollover seams, honoring explicit limits.

    Without an explicit cycle limit, only an unambiguous adjacent N -> 1
    transition is accepted.  This uses source order and never the wall clock.
    """
    normalized = [normalize_issue(issue) for issue in issues]
    expected_previous = None
    if target:
        cycle = cycle_key(target.get('cycle_id'))
        lengths = target_cycle_lengths(target)
        if cycle and int(cycle) > 1:
            expected_previous = lengths.get(str(int(cycle) - 1))
    candidates = []
    for index in range(1, len(normalized)):
        previous = int(normalized[index - 1])
        current = int(normalized[index])
        if current != 1 or previous <= 1:
            continue
        if expected_previous is not None and previous != expected_previous:
            continue
        candidates.append(index)
    return candidates


def target_identity(target):
    """A shared listing URL may host multiple configured columns/authors."""
    import hashlib
    import json
    from kill_numbers.text_utils import normalize_keyword, as_list
    if target.get('target_id'):
        return str(target['target_id'])
    value = {'url': canonical_url(target['url']),
             'anchor': [normalize_keyword(v) for v in as_list(target.get('anchor'))],
             'identity': normalize_keyword(str(target.get('article_identity') or '')),
             'parser': target.get('special_parser') or '',
             'source': target.get('source_url_pattern') or ''}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
