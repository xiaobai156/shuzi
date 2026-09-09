"""One completeness gate shared by file, live, and cache duplicate checking."""
from collections import defaultdict
from enum import Enum
import re
import unicodedata

from kill_numbers.domain.periods import canonical_url, cycle_key, period_key, period_sort_key, recent_periods, target_identity
from kill_numbers.text_utils import normalize_issue


class DetectionStatus(str, Enum):
    CLEAN = '完整且未发现重复'
    SUSPECT = '完整但存在疑似重复'
    REJECT = '完整且达到拒收条件'
    INCOMPLETE = '检测未完成'


EXIT_CODES = {DetectionStatus.CLEAN: 0, DetectionStatus.INCOMPLETE: 4,
              DetectionStatus.SUSPECT: 5, DetectionStatus.REJECT: 6}


def normalize_numbers(value):
    if not isinstance(value, str):
        raise ValueError('号码必须是字符串')
    value = unicodedata.normalize('NFKC', value).strip()
    if not re.fullmatch(r'[0-9]{1,2}(?:[\s,.;、，。；|/\\]+[0-9]{1,2})*', value):
        raise ValueError('号码整串格式无效')
    numbers = re.findall(r'[0-9]+', value)
    if any(not 1 <= int(number) <= 49 for number in numbers):
        raise ValueError('号码必须在01至49范围内')
    return ','.join(f'{int(number):02d}' for number in numbers)


def completeness_reasons(records, targets, recent_count=10, *, cycle_lengths=None, latest_by_site=None):
    reasons = []
    if recent_count != 10:
        reasons.append(('全部目标', '', '正式判重必须使用连续近10期'))
    if not targets:
        return [('全部目标', '', '没有启用目标，不能生成完整结论')]
    by_url = defaultdict(list)
    active = {target_identity(t): t for t in targets if not t.get('disabled')}
    refs = {(t['name'], canonical_url(t['url'])): key for key,t in active.items()}
    for record in records:
        url = getattr(record, 'target_id', '') or refs.get((record.name, canonical_url(record.url)), '')
        if url not in active:
            reasons.append((record.name, record.url, '记录未匹配到唯一启用目标'))
        else:
            by_url[url].append(record)
    period_sets = []
    for url, target in active.items():
        name = str(target.get('name') or target['url'])
        values = {}
        for record in by_url[url]:
            try:
                if canonical_url(record.url) != canonical_url(target['url']):
                    raise ValueError('记录URL与当前目标身份不匹配')
                if record.name != name:
                    raise ValueError('记录名称与当前配置身份不匹配')
                numbers = normalize_numbers(record.numbers).split(',')
                if type(target.get('count')) is not int or len(numbers) != target['count']:
                    raise ValueError('号码数量与配置不匹配')
                if not target.get('allow_duplicate_numbers', False) and len(set(numbers)) != len(numbers):
                    raise ValueError('号码有重复')
                cycle = cycle_key(getattr(record, 'cycle_id', ''))
                if not cycle:
                    raise ValueError('缺少明确cycle_id，不能确认数据属于同一周期')
                if target.get('cycle_id') and int(cycle) > int(cycle_key(target['cycle_id'])):
                    raise ValueError('缓存周期超出明确指定的当前周期')
                key = period_key(cycle, record.issue)
                if key in values and values[key] != ','.join(numbers):
                    raise ValueError('同站同期候选冲突')
                values[key] = ','.join(numbers)
            except ValueError as exc:
                reasons.append((name, target['url'], str(exc)))
        if not values:
            reasons.append((name, target['url'], '没有有效的已确认周期记录'))
            continue
        latest = (latest_by_site or {}).get(url) or max(values, key=period_sort_key)
        try:
            expected = set(recent_periods(latest, 10, cycle_lengths))
        except ValueError as exc:
            reasons.append((name, target['url'], str(exc)))
            continue
        if target.get('cycle_id') and latest.split(':', 1)[0] != cycle_key(target['cycle_id']):
            reasons.append((name, target['url'], '缓存最新周期与明确指定周期不一致'))
        unexpected = values.keys() - expected
        if unexpected:
            reasons.append((name, target['url'], '包含近10期窗口以外记录，不能混入正式比较'))
        missing = expected - values.keys()
        if missing:
            reasons.append((name, target['url'], '连续近10期不完整，缺少：'+','.join(sorted(missing,key=period_sort_key))))
        period_sets.append((name, target['url'], expected & values.keys()))
    # No common periods is not a clean comparison, even if both sites have ten.
    for i, left in enumerate(period_sets):
        for right in period_sets[i+1:]:
            if not left[2] & right[2]:
                reasons.append((left[0], left[1], f'与 {right[0]} 没有共同期号，检测未完成'))
    return list(dict.fromkeys(reasons))


def status_for_detection(problems, region_problems, bad_lines, records, matches):
    if problems or region_problems or bad_lines or not records:
        return DetectionStatus.INCOMPLETE
    if any(match.status == 'reject' for match in matches):
        return DetectionStatus.REJECT
    if matches:
        return DetectionStatus.SUSPECT
    return DetectionStatus.CLEAN
