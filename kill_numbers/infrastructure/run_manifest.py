"""Tie output files to one completed run; file existence is never proof of success."""
import hashlib
import json
from pathlib import Path

from kill_numbers.infrastructure.file_store import atomic_write_json
from kill_numbers.infrastructure.cache_repository import target_signature
from kill_numbers.domain.periods import canonical_url, cycle_key, target_identity


def file_digest(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _result_entry(result, target, cycle):
    return {
        'name': result.name,
        'url': result.url,
        'cycle_id': cycle,
        'target_id': target_identity(target),
        'contract_hash': target_signature(target),
    }


def _failure_entry(failure, target):
    return {
        'name': failure.name,
        'url': failure.url,
        'reason': failure.reason,
        'target_id': target_identity(target),
    }


def _target_by_result_ref(targets):
    return {(t['name'], canonical_url(t['url'])): t for t in targets}


def write_run_manifest(path, run_id, issue, results, failures, targets, success_file, failed_file):
    by_ref = _target_by_result_ref(targets)
    cycles = {
        cycle_key(target.get('cycle_id'))
        for target in targets
        if cycle_key(target.get('cycle_id'))
    }
    if len(cycles) > 1:
        raise ValueError('一次单期运行不能混用多个 cycle_id')
    cycle = next(iter(cycles), '')
    value = {
        'version': 1,
        'run_id': run_id,
        'issue': str(issue),
        'cycle_id': cycle,
        'outputs_finalized': True,
        'files': [
            {'path': str(Path(file_path).resolve()), 'sha256': file_digest(file_path)}
            for file_path in (success_file, failed_file)
        ],
        'results': [
            _result_entry(
                result,
                by_ref[(result.name, canonical_url(result.url))],
                cycle,
            )
            for result in results
        ],
        'failures': [
            _failure_entry(
                failure,
                by_ref[(failure.name, canonical_url(failure.url))],
            )
            for failure in failures
        ],
    }
    atomic_write_json(path, value)
    return value


def _read_completed_manifest(path, issue):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if (
        not isinstance(value, dict)
        or value.get('version') != 1
        or value.get('issue') != str(issue)
        or value.get('outputs_finalized') is not True
    ):
        raise ValueError('期数不匹配或输出未定稿')
    files = value.get('files')
    if not isinstance(files, list) or len(files) != 2:
        raise ValueError('输出文件证据不完整')
    if not isinstance(files[0], dict) or not files[0].get('sha256'):
        raise ValueError('本轮成功文件缺失，输出未定稿')
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get('path'), str):
            raise ValueError('输出文件证据格式错误')
        if file_digest(item['path']) != item.get('sha256'):
            raise ValueError('输出文件在运行后被修改')
    if not isinstance(value.get('results'), list) or not isinstance(value.get('failures'), list):
        raise ValueError('运行结果清单无效')
    return value


def read_run_manifest_for_issue(path, issue):
    """Read one finalized manifest without needing its caller-generated run_id."""
    try:
        return _read_completed_manifest(path, issue)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError(f'本轮输出不可用：{exc}') from exc


def read_run_manifest_for_retry(path, issue):
    """Validate a finalized pre-retry manifest, including all-failed runs."""
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        if (
            not isinstance(value, dict)
            or value.get('version') != 1
            or value.get('issue') != str(issue)
            or value.get('outputs_finalized') is not True
        ):
            raise ValueError('期数不匹配或输出未定稿')
        files = value.get('files')
        if not isinstance(files, list) or len(files) != 2:
            raise ValueError('输出文件证据不完整')
        for item in files:
            if not isinstance(item, dict) or not isinstance(item.get('path'), str):
                raise ValueError('输出文件证据格式错误')
            if file_digest(item['path']) != item.get('sha256'):
                raise ValueError('输出文件在运行后被修改')
        if not isinstance(value.get('results'), list) or not isinstance(value.get('failures'), list):
            raise ValueError('运行结果清单无效')
        return value
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError(f'本轮输出不可用：{exc}') from exc


def read_run_manifest(path, run_id, issue):
    try:
        value = _read_completed_manifest(path, issue)
        if value.get('run_id') != run_id:
            raise ValueError('运行标识不匹配')
        return value
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError(f'本轮输出不可用：{exc}') from exc


def refresh_run_manifest_after_retry(
    path,
    manifest,
    issue,
    successful_results,
    targets,
    success_file,
    failed_file,
):
    """Rebind hashes and site state after a locked retry edits the two TXT files."""
    if (
        not isinstance(manifest, dict)
        or manifest.get('version') != 1
        or manifest.get('issue') != str(issue)
        or manifest.get('outputs_finalized') is not True
    ):
        raise ValueError('重抓运行清单结构、期数或状态不匹配')
    by_ref = _target_by_result_ref(targets)
    cycle = cycle_key(manifest.get('cycle_id'))

    result_entries = {}
    for entry in manifest.get('results', []):
        if not isinstance(entry, dict) or not entry.get('target_id'):
            raise ValueError('重抓运行清单 result 身份无效')
        result_entries[entry['target_id']] = dict(entry)
    failure_entries = {}
    for entry in manifest.get('failures', []):
        if not isinstance(entry, dict) or not entry.get('target_id'):
            raise ValueError('重抓运行清单 failure 身份无效')
        failure_entries[entry['target_id']] = dict(entry)

    for result in successful_results:
        target = by_ref.get((result.name, canonical_url(result.url)))
        if target is None:
            raise ValueError(f'重抓结果未匹配当前启用目标：{result.name}')
        target_cycle = cycle_key(target.get('cycle_id'))
        if target_cycle and cycle and target_cycle != cycle:
            raise ValueError('重抓结果周期与原运行清单冲突')
        target_id = target_identity(target)
        result_entries[target_id] = _result_entry(result, target, cycle)
        failure_entries.pop(target_id, None)

    value = dict(manifest)
    value['files'] = [
        {'path': str(Path(file_path).resolve()), 'sha256': file_digest(file_path)}
        for file_path in (success_file, failed_file)
    ]
    value['results'] = list(result_entries.values())
    value['failures'] = list(failure_entries.values())
    atomic_write_json(path, value)
    return value
