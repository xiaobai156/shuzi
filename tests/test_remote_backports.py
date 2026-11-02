"""Remote audit regressions adapted to the local v1 cache and site contracts."""
from dataclasses import replace

import pytest
import crawler
from kill_numbers.acquisition.documents import make_source_document
from kill_numbers.application.batch_service import iter_completed_batch
from kill_numbers.domain.models import CrawlResult, CrawlFailure
from kill_numbers.infrastructure.cache_repository import update_recent_duplicate_cache
from kill_numbers.infrastructure.cache_format import records_from_cache_document
from kill_numbers.parsing.common import extract_issue_numbers, find_number_groups
from kill_numbers.validation.result_validator import evidence_from_source_document, validate_crawl_results
import json


@pytest.mark.parametrize('bad', ['00','50','99','101'])
def test_no_truncated_numeric_run(bad):
    assert find_number_groups('215期 绝杀十码 [01 02 03 04 05 06 07 08 09 10 '+bad+']') == []


def test_fourth_conflicting_group_is_not_hidden():
    with pytest.raises(ValueError, match='候选不唯一'):
        extract_issue_numbers('215期 栏目 [01 02 03] [01 02 03] [01 02 03] [04 05 06]', ['215'],
                              keywords=['栏目'], expected_count=3, region='top', strict_ambiguous=True)


@pytest.mark.parametrize('anchor', ['起点', None])
def test_stop_anchor_is_required(anchor):
    with pytest.raises(ValueError, match='结束锚点'):
        extract_issue_numbers('起点\n215期 01 02 03', ['215'], anchor=anchor, stop_anchor='终点')


def target(name='甲', url='https://a.test'):
    return dict(name=name, url=url, count=3)


def test_evidence_cannot_fall_back_to_page():
    t = dict(target(), anchor='没有此栏目')
    doc = make_source_document(kind='page', url=t['url'], content='215期 01 02 03', priority=100)
    with pytest.raises(ValueError):
        evidence_from_source_document(t, '215', ['01','02','03'], doc)


def test_evidence_fingerprint_must_match_content():
    t=target()
    doc = make_source_document(kind='page', url=t['url'], content='215期 01 02 03', priority=100)
    with pytest.raises(ValueError, match='指纹'):
        evidence_from_source_document(t, '215', ['01','02','03'], replace(doc, fingerprint='wrong'))


def test_normalized_numbers_keep_evidence():
    t=target()
    doc = make_source_document(kind='page', url=t['url'], content='215期 01 02 03', priority=100)
    e = evidence_from_source_document(t, '215', ['01','02','03'], doc)
    accepted, reason = validate_crawl_results(t,['215'],[CrawlResult(t['url'],t['name'],'215',['1','2','3'],e)])
    assert not reason and accepted[0].evidence is e


def test_per_site_retention_and_subset_preservation(tmp_path):
    a,b=target(),target('乙','https://b.test')
    path=tmp_path/'cache.json'
    def result(t,i): return CrawlResult(t['url'],t['name'],str(i),['01','02','03'])
    update_recent_duplicate_cache(path,[result(b,i) for i in range(206,216)], [str(i) for i in range(206,216)], targets=[a,b])
    old=json.loads(path.read_text(encoding='utf-8'))
    update_recent_duplicate_cache(path,[result(a,221)],['221'],targets=[a])
    new=json.loads(path.read_text(encoding='utf-8'))
    assert next(s for s in new['sites'] if s['name']=='乙') == old['sites'][0]
    assert {r['issue'] for r in records_from_cache_document(new) if r['name']=='乙'} == {str(i) for i in range(206,216)}


def test_cache_rejects_same_run_success_failure(tmp_path):
    p=tmp_path/'cache.json';t=target()
    with pytest.raises(ValueError, match='同时'):
        update_recent_duplicate_cache(p,[CrawlResult(t['url'],t['name'],'215',['01','02','03'])],['215'],
            failures=[CrawlFailure(t['url'],t['name'],'失败')],targets=[t])
    assert not p.exists()


@pytest.mark.parametrize('bad',['0','-1','1期2期','12.3','True','1000'])
def test_issue_boundary(bad):
    with pytest.raises(ValueError): crawler.normalize_issue(bad)


def test_keyboard_interrupt_propagates():
    def interrupt(_): raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt): list(iter_completed_batch([1],interrupt,1))


@pytest.mark.parametrize('field',['disabled','allow_ambiguous','allow_duplicate_numbers'])
def test_config_boolean_strings_are_rejected(tmp_path, field):
    p=tmp_path/'targets.json'
    p.write_text(json.dumps([dict(target(),region='top',**{field:'false'})]),encoding='utf-8')
    with pytest.raises(ValueError,match='布尔'): crawler.load_targets(p)


def test_formal_success_filename_supplies_period(tmp_path):
    import check_duplicates
    p=tmp_path/'215期-杀数字-成功.txt';p.write_text('01,02,03 甲\n',encoding='utf-8')
    records,bad=check_duplicates.read_records([p])
    assert not bad and records[0].issue=='215期'
    p.write_text('01,02,03 甲 214期\n',encoding='utf-8')
    records,bad=check_duplicates.read_records([p])
    assert not records and bad


def test_macau_group_rejects_bad_token():
    from kill_numbers.parsing.dedicated.site_parsers import extract_macau_baoma_numbers
    html='<span class="am_qi">215期</span><span class="am_yc">01 02 03 50</span>'
    assert extract_macau_baoma_numbers(html,['215'],expected_count=3,region='top')=={}


def test_single_prompt_rejects_multiple_periods():
    import run_crawler_prompt
    with pytest.raises(ValueError,match='单期'): run_crawler_prompt.crawler_command_for_input('215,216')


def test_current_cycle_never_borrows_legacy_rows():
    t = dict(target(), region='top', anchor='作者甲', article_title_anchor='栏目',
             stop_anchor='结束', keywords=['杀码'], issue_position_window=3)
    page = '栏目215期\n作者甲\n215期 杀码 01 02 03\n365期 杀码 01 02 03\n001期 杀码 04 05 06\n214期 杀码 07 08 09\n结束'
    assert crawler.extract_top_article_history_current_cycle_numbers(page, ['214','215'], t) == {'215':['01','02','03']}


@pytest.mark.parametrize('conflict', [False, True])
def test_link_chain_resolves_document_base_and_rejects_cross_priority_conflict(monkeypatch, conflict):
    from test_zuojianzifu_link_chain import TARGET
    t = dict(TARGET)
    doc = make_source_document(kind='decoded_script_stream', url='https://source.test/dir/index.html',
        content='<a href="article.html">215期 作茧自缚</a>', priority=100)
    other = replace(doc, url='https://other.test/index.html', priority=50)
    article = make_source_document(kind='page', url='https://source.test/dir/article.html',
        content='作茧自缚\n215期 杀特十码 01 02 03 04 05 06 07 08 09 10', priority=100)
    calls=[]
    def discover(url):
        calls.append(url)
        if url == t['url']: return '列表', [doc, other] if conflict else [doc]
        assert url == article.url
        return '作茧自缚', [article]
    monkeypatch.setattr(crawler, 'discover_static_documents', discover)
    if conflict:
        with pytest.raises(ValueError, match='链接'): crawler.crawl_zuojianzifu_link_chain(t,['215'])
        assert calls == [t['url']]
    else:
        assert crawler.crawl_zuojianzifu_link_chain(t,['215'])[2]['215'] == [f'{i:02d}' for i in range(1,11)]


def test_multi_report_does_not_trust_old_success_file(tmp_path, monkeypatch):
    import run_crawler_multi_prompt as multi
    monkeypatch.setattr(crawler,'load_targets',lambda: [target()])
    success=tmp_path/'success.txt';failure=tmp_path/'failure.txt'
    success.write_text('01,02,03 甲\n',encoding='utf-8')
    run=multi.IssueRun('215',1,success,failure)
    assert multi.write_multi_failure_report([run], tmp_path/'report.txt') == ['甲']


@pytest.mark.parametrize('page,issue', [
    ('栏目\n215期 其他字段 01 02 03', '215'),
    ('栏目\n215期 杀码 01 02 03\n214期 杀码 04 05 06\n213期 杀码 07 08 09\n212期 杀码 01 02 03','212'),
])
def test_evidence_requires_field_and_window(page, issue):
    t=dict(target(), anchor='栏目', keywords=['杀码'], region='top', issue_position_window=3)
    doc=make_source_document(kind='page',url=t['url'],content=page,priority=100)
    with pytest.raises(ValueError): evidence_from_source_document(t,issue,['01','02','03'],doc)


def test_arbitrary_document_identity_cannot_disable_anchor():
    t=dict(target(),anchor='甲',region='top')
    doc=make_source_document(kind='page',url=t['url'],content='215期 01 02 03',priority=100,identity='甲')
    assert crawler.parse_target_documents([doc],t,['215'])[0] == {}


@pytest.mark.parametrize('invalid', ['wrong_issue', 'wrong_count', 'disabled', 'unknown'])
def test_cache_rejects_invalid_incoming_records(tmp_path, invalid):
    t=target();p=tmp_path/'cache.json'
    r=CrawlResult(t['url'],t['name'],'215',['01','02','03'])
    if invalid=='wrong_issue': r.issue='214'
    if invalid=='wrong_count': r.numbers=['01','02']
    if invalid=='disabled': t['disabled']=True
    if invalid=='unknown': r.name='其他站点'
    with pytest.raises(ValueError): update_recent_duplicate_cache(p,[r],['215'],targets=[t])
    assert not p.exists()


def test_cache_reader_accepts_independent_site_windows(tmp_path):
    import check_duplicates as duplicates
    a,b=target(),target('乙','https://b.test');p=tmp_path/'cache.json'
    results=[CrawlResult(t['url'],t['name'],str(i),['01','02','03']) for t,start in [(a,212),(b,206)] for i in range(start,start+10)]
    update_recent_duplicate_cache(p,results,[str(i) for i in range(206,222)],targets=[a,b])
    assert len(duplicates.validate_cache_data(json.loads(p.read_text(encoding='utf-8')),p,10,[a,b]))==20


def test_subset_cache_preserves_site_order_and_unique_ids(tmp_path):
    a,b=target(),target('乙',target()['url']);p=tmp_path/'cache.json'
    def result(t): return CrawlResult(t['url'],t['name'],'215',['01','02','03'])
    update_recent_duplicate_cache(p,[result(a)],['215'],targets=[a])
    first=json.loads(p.read_text(encoding='utf-8'))['sites'][0]
    update_recent_duplicate_cache(p,[result(b)],['215'],targets=[b])
    sites=json.loads(p.read_text(encoding='utf-8'))['sites']
    assert sites[0]==first
    assert len({s['id'] for s in sites})==2


def test_duplicate_cli_incomplete_files_cannot_report_complete(tmp_path,monkeypatch):
    import check_duplicates as duplicates
    import sys
    p=tmp_path/'215期-杀数字-成功.txt';p.write_text('01,02,03 甲\n',encoding='utf-8')
    output=tmp_path/'report.txt'
    monkeypatch.setattr(sys,'argv',['check_duplicates.py',str(p),'--output',str(output)])
    monkeypatch.setattr(duplicates,'add_urls',lambda records: None)
    assert duplicates._main_unlocked()!=0
    assert '检测未完成' in output.read_text(encoding='utf-8')


@pytest.mark.parametrize('region,wanted', [('top','212'),('bottom','215')])
def test_user_sequence_window_counts_rows_not_posts(region,wanted):
    t=dict(target(),anchor='甲',region=region,issue_position_window=3)
    docs=[make_source_document(kind='user_forum_topic',url=f'https://a.test/topic/{index}',
        content='甲\n'+'\n'.join(f'{i}期 01 02 03' for i in issues), priority=100,
        metadata={'region_sequence':'user_forum_topics','region_index':index})
        for index,issues in enumerate([[215,214],[213,212]])]
    assert crawler.parse_target_documents(docs,t,[wanted])[0]=={}


def test_disabled_target_never_fetches(monkeypatch):
    def unexpected(*args): raise AssertionError('封存站点不应抓取')
    monkeypatch.setattr(crawler,'fetch_target_documents',unexpected)
    results,failure=crawler.crawl_one(dict(target(),anchor='甲',disabled=True),['215'])
    assert not results and '停用' in failure.reason


def test_formal_pipeline_http_and_custom_name(tmp_path,monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            body='<html><meta charset="utf-8"><body>栏目\n215期 杀码 01 02 03</body></html>'.encode()
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Page)
    worker=Thread(target=server.serve_forever,daemon=True);worker.start()
    monkeypatch.setattr(crawler,'save_debug_page',lambda *args: None)
    try:
        t=dict(target('自定义 名称准',f'http://127.0.0.1:{server.server_port}/article'),anchor='栏目',keywords=['杀码'],region='top')
        results,failure=crawler.crawl_one(t,['215'])
        assert failure is None and results[0].name == t['name']
        assert results[0].numbers==['01','02','03']
        assert validate_crawl_results(t,['215'],results)[1]==''
        results,failure=crawler.crawl_one(t,['214'])
        assert not results and failure
    finally:
        server.shutdown();server.server_close();worker.join(timeout=3)


@pytest.mark.parametrize('mode',['valid','missing','wrong_issue','changed_output','bad_json'])
def test_multi_receipt_validation_and_cleanup(tmp_path,monkeypatch,mode):
    import run_crawler_multi_prompt as multi
    import hashlib
    from types import SimpleNamespace
    success=tmp_path/'success.txt';failed=tmp_path/'failed.txt'
    success.write_bytes(b'previous\r\n')
    monkeypatch.setattr(crawler,'output_files_for_issues',lambda issues:(success,failed,tmp_path/'unused'))
    receipts=[]
    def launch(cmd,**kwargs):
        assert '--no-cache-update' in cmd
        receipt=__import__('pathlib').Path(cmd[cmd.index('--run-result')+1]);receipts.append(receipt)
        if mode=='missing': return SimpleNamespace(returncode=0)
        payload={'issue':'214' if mode=='wrong_issue' else '215',
            'successes':[['甲','https://a.test','contract']],
            'output_hashes':[hashlib.sha256(success.read_bytes()).hexdigest(),None]}
        receipt.write_text('broken' if mode=='bad_json' else json.dumps(payload),encoding='utf-8')
        if mode=='changed_output': success.write_bytes(b'changed')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(multi.subprocess,'run',launch)
    run=multi.run_issue('215')
    assert (run.returncode==0)==(mode=='valid')
    assert (run.current_successes is not None)==(mode=='valid')
    assert not receipts[0].parent.exists()


def test_formal_entry_receipt_uses_only_current_results(tmp_path,monkeypatch):
    import sys
    import hashlib
    t=dict(target(),anchor='甲',region='top')
    receipt=tmp_path/'receipt.json';success=tmp_path/'success.txt';failed=tmp_path/'failed.txt'
    success.write_bytes('01,02,03 甲\r\n'.encode())
    monkeypatch.setattr(crawler,'TARGETS',[t])
    monkeypatch.setattr(crawler,'output_files_for_issues',lambda issues:(str(success),str(failed),str(tmp_path/'unused')))
    monkeypatch.setattr(crawler,'crawl_targets',lambda *args,**kwargs: ({0:[]},{0:CrawlFailure(t['url'],t['name'],'本轮失败')}))
    monkeypatch.setattr(sys,'argv',['crawler.py','--issues','215','--no-cache-update','--run-result',str(receipt)])
    assert crawler._main_unlocked()==1
    payload=json.loads(receipt.read_text(encoding='utf-8'))
    assert payload['successes']==[]
    assert payload['output_hashes']==[hashlib.sha256(p.read_bytes()).hexdigest() for p in (success,failed)]
