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


def write_run_manifest(path, run_id, issue, results, failures, targets, success_file, failed_file):
    by_ref = {(t['name'], canonical_url(t['url'])): t for t in targets}
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
            {
                'name': result.name,
                'url': result.url,
                'cycle_id': cycle,
                'target_id': target_identity(
                    by_ref[(result.name, canonical_url(result.url))]
                ),
                'contract_hash': target_signature(
                    by_ref[(result.name, canonical_url(result.url))]
                ),
            }
            for result in results
        ],
        'failures': [
            {
                'name': failure.name,
                'url': failure.url,
                'reason': failure.reason,
                'target_id': target_identity(
                    by_ref[(failure.name, canonical_url(failure.url))]
                ),
            }
            for failure in failures
        ],
    }
    atomic_write_json(path, value)
    return value


def read_run_manifest(path, run_id, issue):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        if (not isinstance(value, dict) or value.get('version') != 1 or value.get('run_id') != run_id
                or value.get('issue') != str(issue) or value.get('outputs_finalized') is not True):
            raise ValueError('运行标识/期数不匹配或输出未定稿')
        files = value.get('files')
        if not isinstance(files, list) or len(files) != 2:
            raise ValueError('输出文件证据不完整')
        if not isinstance(files[0], dict) or not files[0].get('sha256'):
            raise ValueError('本轮成功文件缺失，输出未定稿')
        for item in files:
            if file_digest(item['path']) != item['sha256']:
                raise ValueError('输出文件在运行后被修改')
        if not isinstance(value.get('results'), list) or not isinstance(value.get('failures'), list):
            raise ValueError('运行结果清单无效')
        return value
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError(f'本轮输出不可用：{exc}') from exc
