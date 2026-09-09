"""Version-2 rolling observations; this store NEVER supplies live crawl results."""
import hashlib
import json
import time
from collections import defaultdict
from pathlib import Path

from kill_numbers.domain.periods import (
    canonical_url, cycle_key, period_key, period_sort_key, recent_periods,
    validate_cycle_lengths, target_identity,
)
from kill_numbers.infrastructure.file_store import atomic_write_json
from kill_numbers.text_utils import normalize_issue


CACHE_VERSION = 2


def target_signature(target):
    # Preserve the legacy hash for targets whose effective contract did not
    # change, while binding every newly supported acquisition/scope field when
    # it is explicitly present. This invalidates only the affected site cache.
    legacy_fields = (
        'url', 'count', 'region', 'anchor', 'stop_anchor', 'keywords',
        'special_parser', 'article_identity', 'source_url_pattern',
        'source_anchor', 'issue_position_window', 'allow_duplicate_numbers',
        'api_url', 'article_title_anchor', 'encoding', 'link_keywords',
        'pagination_limit', 'insecure_tls', 'allowed_resource_hosts',
    )
    new_contract_fields = (
        'position', 'pagination_next_text', 'section_id', 'content_class',
        'allowed_source_types', 'browser', 'browser_ready_selector',
        'browser_fallback', 'max_response_bytes',
    )
    value = {key: target.get(key) for key in legacy_fields}
    value.update(
        {key: target.get(key) for key in new_contract_fields if key in target}
    )
    value['url'] = canonical_url(value['url'] or '')
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _identity(item):
    url = canonical_url(item.get('url') or '')
    if not url:
        raise ValueError('缓存记录缺少站点 URL，不能仅按名称确定身份')
    return str(item.get('target_id') or url + '|legacy-name=' + str(item.get('name') or ''))


def _key(item):
    return _identity(item), period_key(item.get('cycle_id'), item.get('issue'))


def _read(path):
    if not path.exists():
        return {'version': CACHE_VERSION, 'records': [], 'failures': [], 'sites': {}, 'cycle_lengths': {}}
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise ValueError(f'缓存文件无法读取，已停止覆盖：{path}；{exc}') from exc
    if not isinstance(data, dict) or type(data.get('version')) is not int or data.get('version') not in (1, CACHE_VERSION):
        raise ValueError('缓存文件结构或版本错误，已停止覆盖')
    for field in ('records', 'failures'):
        if not isinstance(data.get(field, []), list) or any(not isinstance(r, dict) for r in data.get(field, [])):
            raise ValueError(f'缓存文件 {field} 格式错误，已停止覆盖')
    if not isinstance(data.get('sites', {}), dict):
        raise ValueError('缓存 sites 格式错误')
    validate_cycle_lengths(data.get('cycle_lengths', {}))
    return data


def update_recent_duplicate_cache(cache_path, results, issues, recent_count=10,
                                  failures=None, *, targets=None, cycle_id=None,
                                  cycle_lengths=None):
    if type(recent_count) is not int or recent_count <= 0:
        raise ValueError('recent_count 必须是正整数')
    requested = list(dict.fromkeys(normalize_issue(i) for i in issues))
    if not requested:
        return
    path = Path(cache_path)
    data = _read(path)
    targets_by_url = {target_identity(t): t for t in (targets or []) if not t.get('disabled')}
    targets_by_ref = {(t['name'], canonical_url(t['url'])): t for t in targets_by_url.values()}
    lengths = validate_cycle_lengths(data.get('cycle_lengths', {}))
    for cycle, length in validate_cycle_lengths(cycle_lengths or {}).items():
        if cycle in lengths and lengths[cycle] != length:
            raise ValueError('已记录的周期期数上限发生冲突，拒绝覆盖')
        lengths[cycle] = length
    fallback_cycle = cycle_key(cycle_id)
    sites = dict(data.get('sites', {}))
    old_success, old_failure = {}, {}
    for field, bucket in [('records', old_success), ('failures', old_failure)]:
        for original in data.get(field, []):
            row = dict(original)
            if targets is not None and not row.get('target_id'):
                target = targets_by_ref.get((row.get('name'), canonical_url(row.get('url') or '')))
                if target:
                    row['target_id'] = target_identity(target)
            key = _key(row)
            if targets is not None and key[0] not in targets_by_url:
                continue  # Disabled targets do not survive in the active baseline.
            if key in bucket and bucket[key] != row:
                raise ValueError(f'缓存同站同期冲突：{row.get("name")} {row.get("issue")}期')
            bucket[key] = row
    # A previously contradictory store is not silently re-certified.
    if old_success.keys() & old_failure.keys():
        raise ValueError('缓存同站同期同时存在成功和失败，需重建该缓存')
    new_success, new_failure = {}, {}

    def base(item, issue):
        target = targets_by_ref.get((str(item.name), canonical_url(item.url)), {})
        if targets is not None and not target:
            raise ValueError('缓存写入包含未启用目标')
        url = target_identity(target) if target else canonical_url(item.url) + '|legacy-name=' + str(item.name)
        cycle = cycle_key(target.get('cycle_id') or fallback_cycle)
        row = {'name': str(item.name), 'url': str(item.url), 'issue': normalize_issue(issue)}
        if target:
            row['target_id'] = target_identity(target)
        if cycle:
            row['cycle_id'] = cycle
        if cycle in lengths and int(row['issue']) > lengths[cycle]:
            raise ValueError('期号超过已确认的周期期数上限')
        meta = dict(sites.get(url, {}))
        if target:
            signature = target_signature(target)
            if meta.get('contract_hash') and meta['contract_hash'] != signature:
                # Contract changes require fresh observations, not reuse by URL.
                for bucket in (old_success, old_failure):
                    for key in list(bucket):
                        if key[0] == url:
                            del bucket[key]
            meta.update(contract_hash=signature, region=target.get('region'), name=target.get('name'))
        meta['cycle_verified'] = bool(cycle)
        if cycle:
            meta['cycle_id'] = cycle
        sites[url] = meta
        return row

    for result in results:
        if normalize_issue(result.issue) not in requested:
            raise ValueError('缓存写入混入非指定期数')
        row = base(result, result.issue)
        tokens = list(result.numbers)
        if not tokens or any(not isinstance(n, str) or len(n) != 2 or not n.isdecimal()
                             or not 1 <= int(n) <= 49 for n in tokens):
            raise ValueError('缓存成功号码必须是完整的01至49号码组')
        target = targets_by_ref.get((str(result.name), canonical_url(result.url)), {})
        if target.get('count') is not None and len(tokens) != target['count']:
            raise ValueError('缓存号码数量与配置不匹配')
        if not target.get('allow_duplicate_numbers', False) and len(set(tokens)) != len(tokens):
            raise ValueError('缓存号码有重复')
        row['numbers'] = ','.join(tokens)
        key = _key(row)
        if key in new_success and new_success[key]['numbers'] != row['numbers']:
            raise ValueError('缓存更新存在同站同期冲突')
        new_success[key] = row
    for failure in failures or []:
        failure_issues = [getattr(failure, 'issue')] if getattr(failure, 'issue', None) else requested
        for issue in failure_issues:
            if normalize_issue(issue) not in requested:
                raise ValueError('缓存失败状态混入非指定期数')
            row = base(failure, issue)
            row.update(status='failed', reason=str(failure.reason or '未提供失败原因'))
            key = _key(row)
            if key in new_failure and new_failure[key] != row:
                raise ValueError('缓存更新存在同站同期失败冲突')
            new_failure[key] = row
    success_keys, failure_keys = frozenset(new_success), frozenset(new_failure)
    if success_keys & failure_keys:
        raise ValueError('缓存更新同站同期同时存在成功和失败')
    for key in failure_keys:
        old_success.pop(key, None)
    for key in success_keys:
        old_failure.pop(key, None)
    old_success.update(new_success)
    old_failure.update(new_failure)

    # Trim each site's own scope independently. A failed request for 220 must
    # not discard another site's valid 206..215 history.
    groups = defaultdict(list)
    for key in old_success:
        groups[key[0]].append(key)
    retained = set()
    for url, keys in groups.items():
        verified = [k for k in keys if k[1].split(':', 1)[0]]
        candidates = verified or keys
        latest = max(candidates, key=lambda k: period_sort_key(k[1]))[1]
        try:
            wanted = set(recent_periods(latest, recent_count, lengths))
            retained.update(k for k in candidates if k[1] in wanted)
        except ValueError:
            # Keep observations, but a checker will report the unproven seam.
            retained.update(sorted(candidates, key=lambda k: period_sort_key(k[1]))[-recent_count:])
        sites.setdefault(url, {})['latest_period'] = latest
    failure_groups = defaultdict(list)
    for key in old_failure:
        failure_groups[key[0]].append(key)
    retained_failures = {key for keys in failure_groups.values()
                         for key in sorted(keys, key=lambda k: period_sort_key(k[1]))[-recent_count:]}
    if targets is not None:
        sites = {url: meta for url, meta in sites.items() if url in targets_by_url}
    atomic_write_json(path, {
        'version': CACHE_VERSION, 'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'recent_count': recent_count, 'cycle_lengths': lengths, 'sites': sites,
        'records': [row for key, row in old_success.items() if key in retained],
        'failures': [row for key, row in old_failure.items() if key in retained_failures],
        'migration_note': ('未标注cycle_id的旧记录只保留为观察值，不用于正式无重复结论'),
    }, trailing_newline=False)
