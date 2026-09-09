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
