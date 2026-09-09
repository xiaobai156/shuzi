import copy
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import crawler
import check_duplicates as duplicates
import run_crawler_multi_prompt as multi
import retry_failed
from kill_numbers.domain.models import CrawlFailure, CrawlResult
from kill_numbers.domain.periods import target_identity, recent_periods
from kill_numbers.domain.contracts import TargetContract
from kill_numbers.infrastructure.cache_repository import update_recent_duplicate_cache
from kill_numbers.infrastructure.run_manifest import read_run_manifest, write_run_manifest
from kill_numbers.validation.duplicate_gate import completeness_reasons
from kill_numbers.validation.result_validator import evidence_from_source_document
from kill_numbers.acquisition.documents import make_source_document


def target(name='甲', url='https://a.test/topic/1', **extra):
    return dict(name=name, url=url, count=3, anchor=name, region='top',
                keywords=['专属栏目'], issue_position_window=10, cycle_id='2026', **extra)


def success(t, issue, numbers=('01','02','03')):
    return CrawlResult(t['url'], t['name'], str(issue), list(numbers))


def cache(path, targets, start=206, stop=215, same=False):
    results = [success(t, issue, ('01','02','03') if i == 0 or same else ('04','05','06'))
               for i,t in enumerate(targets) for issue in range(start,stop+1)]
    update_recent_duplicate_cache(path, results, list(map(str,range(start,stop+1))), targets=targets)
    return json.loads(path.read_text())


def test_success_clears_old_failure_and_failure_clears_old_success(tmp_path):
    t=target(); p=tmp_path/'cache.json'
    update_recent_duplicate_cache(p, [], ['215'], failures=[CrawlFailure(t['url'], t['name'], '失败')], targets=[t])
    update_recent_duplicate_cache(p, [success(t,215)], ['215'], targets=[t])
    data=json.loads(p.read_text()); assert not data['failures'] and len(data['records'])==1
    update_recent_duplicate_cache(p, [], ['215'], failures=[CrawlFailure(t['url'], t['name'], '失败')], targets=[t])
    data=json.loads(p.read_text()); assert not data['records'] and len(data['failures'])==1


def test_conflicting_new_states_leave_cache_bytes_unchanged(tmp_path):
    t=target(); p=tmp_path/'cache.json'; cache(p,[t]); before=p.read_bytes()
    with pytest.raises(ValueError,match='同时存在成功和失败'):
        update_recent_duplicate_cache(p,[success(t,215)],['215'],targets=[t],
            failures=[CrawlFailure(t['url'],t['name'],'失败')])
    assert p.read_bytes()==before


def test_each_site_keeps_its_own_ten_periods(tmp_path):
    a=target(); b=target('乙','https://b.test'); p=tmp_path/'cache.json'
    cache(p,[a,b]); update_recent_duplicate_cache(p,[success(a,220)],['220'],targets=[a,b])
    data=json.loads(p.read_text())
    assert [r['issue'] for r in data['records'] if r['name']=='乙']==list(map(str,range(206,216)))
    assert [r['issue'] for r in data['records'] if r['name']=='甲']==list(map(str,range(211,216)))+['220']


def test_columns_sharing_listing_url_are_not_merged(tmp_path):
    a=target(); b=target('乙',a['url']); p=tmp_path/'cache.json'
    data=cache(p,[a,b]); assert target_identity(a)!=target_identity(b)
    assert len(data['records'])==20 and len(data['sites'])==2
    assert len(duplicates.load_records_cache(p,10,[a,b]))==20


def test_disabled_site_does_not_survive_active_baseline(tmp_path):
    a=target(); b=target('乙','https://b.test'); p=tmp_path/'cache.json'; cache(p,[a,b])
    b['disabled']=True
    update_recent_duplicate_cache(p,[success(a,216)],['216'],targets=[a,b])
    assert {r['name'] for r in json.loads(p.read_text())['records']}=={'甲'}


def test_contract_change_invalidates_old_observations(tmp_path):
    a=target(); p=tmp_path/'cache.json'; cache(p,[a]); changed={**a,'count':4}
    update_recent_duplicate_cache(p,[success(changed,216,('01','02','03','04'))],['216'],targets=[changed])
    assert len(json.loads(p.read_text())['records'])==1
    with pytest.raises(ValueError,match='不完整'):
        duplicates.load_records_cache(p,10,[changed])


def test_explicit_cycle_boundary_keeps_365_to_1_continuous(tmp_path):
    a=target(); p=tmp_path/'cache.json'; cache(p,[a],359,365)
    new={**a,'cycle_id':'2027'}
    update_recent_duplicate_cache(p,[success(new,i) for i in range(1,4)],['1','2','3'],
        targets=[new],cycle_lengths={'2026':365})
    loaded=duplicates.load_records_cache(p,10,[new]); assert len(loaded)==10
    assert not completeness_reasons(loaded,[new],cycle_lengths={'2026':365})
    assert recent_periods('2027:3',10,{'2026':365})==[
        *[f'2026:{i}' for i in range(359,366)],'2027:1','2027:2','2027:3']


def test_unknown_rollover_never_guesses_last_issue(tmp_path):
    a=target(); p=tmp_path/'cache.json'; cache(p,[a],359,365)
    new={**a,'cycle_id':'2027'}
    update_recent_duplicate_cache(p,[success(new,i) for i in range(1,4)],['1','2','3'],targets=[new])
    with pytest.raises(ValueError,match='跨周期'):
        duplicates.load_records_cache(p,10,[new])


@pytest.mark.parametrize('corruption',['nine','invalid','failure','unknown_cycle','wrong_cycle','v1','wrong_scope'])
def test_incomplete_cache_is_never_accepted(tmp_path,corruption):
    a=target(); p=tmp_path/'cache.json'; data=cache(p,[a])
    if corruption=='nine': data['records'].pop(0)
    elif corruption=='invalid': data['records'][0]['numbers']='01,02,50'
    elif corruption=='failure':
        row=data['records'].pop(); row.pop('numbers'); row.update(status='failed',reason='尚未发布'); data['failures']=[row]
    elif corruption=='unknown_cycle': data['records'][0].pop('cycle_id')
    elif corruption=='wrong_cycle': a['cycle_id']='2027'
    elif corruption=='v1': data['version']=1
    elif corruption=='wrong_scope': a['issue_position_window']=4
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError): duplicates.load_records_cache(p,10,[a])


def test_old_failure_outside_window_does_not_poison_valid_current_history(tmp_path):
    a=target(); p=tmp_path/'cache.json'; data=cache(p,[a])
    data['failures']=[dict(name=a['name'],url=a['url'],target_id=target_identity(a),cycle_id='2026',issue='100',status='failed',reason='旧失败')]
    p.write_text(json.dumps(data))
    assert len(duplicates.load_records_cache(p,10,[a]))==10
    data['failures'][0]['issue']='216'; p.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='失败状态'): duplicates.load_records_cache(p,10,[a])


@pytest.mark.parametrize('scenario,expected',[('clean',0),('missing',4),('reject',6),('unsynced',4)])
def test_formal_cache_cli_returns_honest_status(tmp_path,monkeypatch,scenario,expected):
    a=target(); b=target('乙','https://b.test'); targets=[a,b]
    p=tmp_path/'cache.json'; report=tmp_path/'report.txt'; data=cache(p,targets,same=scenario=='reject')
    if scenario=='missing': data['records'].pop(); p.write_text(json.dumps(data))
    if scenario=='unsynced': crawler.CACHE_STATE_FILE.write_text('{"cache_updated":false}')
    monkeypatch.setattr(crawler,'load_targets',lambda:targets)
    monkeypatch.setattr(sys,'argv',['check_duplicates.py','--from-cache','--cache',str(p),'--output',str(report)])
    assert duplicates._main_unlocked()==expected
    text=report.read_text()
    if expected==4:
        assert '检测未完成' in text
        assert all(phrase not in text for phrase in ('没有发现重复','不重复','检测通过'))


@pytest.mark.parametrize('line',['01,02,03 甲','01 02 03 甲','01.02.03 甲 215期'])
def test_formal_success_filename_supplies_issue(tmp_path,line):
    p=tmp_path/'215期-杀数字-成功.txt';p.write_text(line+'\n')
    records,bad=duplicates.read_records([p])
    assert not bad and len(records)==1 and records[0].issue=='215期'
    assert records[0].numbers=='01,02,03' and records[0].name=='甲'


def test_filename_inline_issue_conflict_fails(tmp_path):
    p=tmp_path/'215期-杀数字-成功.txt';p.write_text('01,02,03 甲 214期\n')
    records,bad=duplicates.read_records([p]);assert not records and bad


def test_unrecognized_input_produces_incomplete_report(tmp_path,monkeypatch):
    p=tmp_path/'215期-杀数字-成功.txt';p.write_text('garbage\n')
    report=tmp_path/'report.txt';monkeypatch.setattr(crawler,'load_targets',lambda:[target()])
    monkeypatch.setattr(sys,'argv',['check_duplicates.py',str(p),'--output',str(report),'--cycle-id','2026'])
    assert duplicates._main_unlocked()==4
    assert '检测未完成' in report.read_text()


def test_failed_run_cannot_reuse_old_success_txt(tmp_path,monkeypatch):
    a=target();monkeypatch.setattr(crawler,'load_targets',lambda:[a])
    p=tmp_path/'215期-杀数字-成功.txt';p.write_text('01,02,03 甲\n')
    run=multi.IssueRun('215',2,p,tmp_path/'fail.txt')
    assert multi.write_multi_failure_report([run],tmp_path/'report.txt')==['甲']


def test_manifest_binds_run_and_output_hashes(tmp_path):
    a=target();p=tmp_path/'ok.txt';f=tmp_path/'fail.txt';m=tmp_path/'run.json';p.write_text('01,02,03 甲\n')
    write_run_manifest(m,'new-run','215',[success(a,215)],[],[a],p,f)
    assert read_run_manifest(m,'new-run','215')['results'][0]['name']=='甲'
    with pytest.raises(ValueError):read_run_manifest(m,'old-run','215')
    p.write_text('modified')
    with pytest.raises(ValueError):read_run_manifest(m,'new-run','215')


def test_retry_preserves_no_newline_old_bytes_and_removes_only_its_block(tmp_path,monkeypatch):
    a=target();monkeypatch.setattr(crawler,'load_targets',lambda:[a]);crawler.RESULTS_DIR.mkdir()
    ok=crawler.RESULTS_DIR/'215期-杀数字-成功.txt';ok.write_bytes('04,05,06 乙'.encode())
    fail=crawler.RESULTS_DIR/'215期-杀数字-失败.txt';fail.write_bytes(('[其他失败] 甲 '+a['url']+' 错误\r\n续行\r\n\r\n[其他失败] 未知 https://unknown.test 错误\r\n').encode())
    doc=make_source_document(kind='page',url=a['url'],priority=100,content='甲\n215期 专属栏目 01 02 03')
    result=success(a,215);result.evidence=evidence_from_source_document(a,'215',result.numbers,doc)
    monkeypatch.setattr(crawler,'crawl_one',lambda *_:([result],None))
    assert retry_failed.retry_failed_file(fail,'215')==(1,1,1)
    assert ok.read_bytes()=='04,05,06 乙\n01,02,03 甲\n'.encode()
    assert fail.read_text().startswith('[其他失败] 未知')
    assert not crawler.CACHE_FILE.exists()


def test_retry_invalid_runner_result_cannot_delete_failure(tmp_path,monkeypatch):
    a=target();monkeypatch.setattr(crawler,'load_targets',lambda:[a]);p=tmp_path/'215期-杀数字-失败.txt';p.write_text(f'[其他失败] 甲 {a["url"]} 错误\n');before=p.read_bytes()
    monkeypatch.setattr(crawler,'crawl_one',lambda *_:([success(a,214)],None))
    assert retry_failed.retry_failed_file(p,'215')[2]!=0 and p.read_bytes()==before


def test_unknown_retry_target_is_not_success(tmp_path,monkeypatch):
    monkeypatch.setattr(crawler,'load_targets',lambda:[target()]);p=tmp_path/'215期-杀数字-失败.txt';p.write_text('[其他失败] 未知 https://unknown.test 错误\n')
    assert retry_failed.retry_failed_file(p,'215')[2]!=0


@pytest.mark.parametrize('key',['disabled','allow_ambiguous','allow_duplicate_numbers'])
def test_json_string_false_is_never_a_boolean(key):
    with pytest.raises(ValueError):TargetContract.from_mapping({'url':'https://a.test',key:'false'})


@pytest.mark.parametrize('issue',['0','-1','1期2期','12.3','True','1000'])
def test_invalid_issue_is_rejected_at_boundary(issue):
    with pytest.raises(ValueError):crawler.normalize_issue(issue)
