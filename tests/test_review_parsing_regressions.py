from dataclasses import replace
import pytest
import crawler
from kill_numbers.acquisition.documents import make_source_document
from kill_numbers.parsing.common import extract_issue_numbers, find_number_groups
from kill_numbers.validation.result_validator import evidence_from_source_document, validate_crawl_results
from kill_numbers.domain.models import CrawlResult


NUMBERS = '01 02 03 04 05 06 07 08 09 10'
TARGET = {'url': 'https://example.test/topic/1', 'name': '专栏',
          'keywords': ['绝杀十码'], 'anchor': '栏目起点', 'stop_anchor': '栏目终点',
          'region': 'top', 'count': 10, 'issue_position_window': 5}


def row(issue, numbers=NUMBERS):
    return f'{issue}期 绝杀十码 [{numbers}] 开00准'


def document(content, **kwargs):
    return make_source_document(kind='page', url=TARGET['url'], content=content,
                                priority=100, **kwargs)


@pytest.mark.parametrize('window', [1, 3, 4, 5, 10, 30])
@pytest.mark.parametrize('region', ['top', 'bottom'])
def test_explicit_window_has_inclusive_last_slot_and_exclusive_next_slot(window, region):
    rows = [row(i) for i in range(100, 135)]
    text = '栏目起点\n'+'\n'.join(rows)+'\n栏目终点'
    target = {**TARGET, 'region': region, 'issue_position_window': window}
    boundary = 100+window-1 if region == 'top' else 135-window
    outside = boundary+1 if region == 'top' else boundary-1
    found = crawler.parse_target_content(text, target, [str(boundary), str(outside)])
    assert set(found) == {str(boundary)}


@pytest.mark.parametrize('bad', ['00', '50', '99', '101'])
def test_invalid_numeric_run_cannot_be_trimmed_into_a_valid_group(bad):
    assert find_number_groups(row(215, NUMBERS+' '+bad)) == []
    assert crawler.parse_target_content('栏目起点\n'+row(215,NUMBERS+' '+bad)+'\n栏目终点',TARGET,['215']) == {}


def test_invalid_rows_do_not_consume_window_slots():
    bad_rows = [row(219,'01 01 03 04 05 06 07 08 09 10'), row(218, NUMBERS+' 50'), row(217,'01 02 03')]
    text = '栏目起点\n'+'\n'.join(bad_rows+[row(215)])+'\n栏目终点'
    assert crawler.parse_target_content(text,{**TARGET,'issue_position_window':1},['215'])['215'] == NUMBERS.split()


def test_fourth_group_in_one_row_is_not_sliced_away():
    text = '栏目起点\n215期 绝杀十码 '+('['+NUMBERS+'] ')*3+'[11 12 13 14 15 16 17 18 19 20]\n栏目终点'
    with pytest.raises(ValueError,match='候选不唯一'):
        crawler.parse_target_content(text,TARGET,['215'])


def test_missing_configured_stop_fails_closed():
    with pytest.raises(ValueError,match='结束锚点'):
        extract_issue_numbers('栏目起点\n'+row(215),['215'], anchor='栏目起点',stop_anchor='栏目终点',expected_count=10)


def test_missing_anchor_cannot_be_replaced_by_matching_numbers_elsewhere():
    with pytest.raises(ValueError):
        evidence_from_source_document(TARGET,'215',NUMBERS.split(),document('别的栏目\n'+row(215)))


def test_outside_window_cannot_obtain_valid_evidence():
    text = '栏目起点\n'+'\n'.join(row(i) for i in range(220,210,-1))+'\n栏目终点'
    with pytest.raises(ValueError,match='窗口'):
        evidence_from_source_document(TARGET,'214',NUMBERS.split(),document(text))


def test_tampered_window_evidence_is_rejected():
    evidence = evidence_from_source_document(TARGET,'215',NUMBERS.split(),document('栏目起点\n'+row(215)+'\n栏目终点'))
    result = CrawlResult(TARGET['url'],TARGET['name'],'215',NUMBERS.split(),replace(evidence,row_rank=5))
    accepted,reason = validate_crawl_results(TARGET,['215'],[result])
    assert not accepted and '窗口' in reason


def test_normalized_numbers_retain_matching_evidence():
    target = {'url':TARGET['url'],'name':'专栏','count':2}
    doc = document('215期 01 02')
    evidence = evidence_from_source_document(target,'215',['1','2'],doc)
    accepted,reason = validate_crawl_results(target,['215'],[CrawlResult(target['url'],target['name'],'215',['1','2'],evidence)])
    assert not reason and accepted[0].evidence is evidence


def test_user_sequence_ignores_irrelevant_documents_and_counts_rows_not_posts():
    target = {**TARGET, 'url':'https://example.test/#/users/123', 'anchor':'作者甲',
              'stop_anchor':'', 'issue_position_window':2}
    docs = [make_source_document(kind='user_forum_topic',url=f'https://example.test/post/{i}',
        content=content,identity='作者甲',priority=100,
        metadata={'region_sequence':'user_forum_topics','region_index':i,
                  'user_id':'123','identity_verified':True,
                  'identity_source':'https://example.test/api/v1/users/123'})
        for i,content in enumerate(['不相关帖子',row(215)+'\n'+row(214),row(213)])]
    found,selected = crawler.parse_target_documents(docs,target,['214'])
    assert found['214']==NUMBERS.split()
    proof = evidence_from_source_document(target,'214',NUMBERS.split(),selected)
    assert proof.scope_kind=='source_identity' and proof.row_rank==1
    assert crawler.parse_target_documents(docs,target,['213']) == ({},None)


def test_document_basename_is_not_a_verified_author_identity():
    target = {**TARGET,'stop_anchor':''}
    doc = replace(document(row(215)),identity='栏目起点')
    assert crawler.parse_target_documents([doc],target,['215']) == ({},None)
