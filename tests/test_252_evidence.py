import hashlib
import unittest

from kill_numbers.domain.models import SourceDocument
from kill_numbers.validation.result_validator import evidence_from_source_document


class DedicatedEvidenceTests(unittest.TestCase):
    def test_hyphen_numbers_keep_complete_group_and_reject_bad_tokens(self):
        from kill_numbers.parsing.common import find_number_groups
        self.assertEqual(find_number_groups('252期:绝杀10码[07-10-13-16-19-30-33-36-39-42]开??准'),
                         [['07','10','13','16','19','30','33','36','39','42']])
        for row in ['00-01-02-03','50-01-02-03','2026-09-09','001-02-03']:
            self.assertEqual(find_number_groups(row),[])

    def test_xinzhu_and_shita_reuse_their_own_boundaries(self):
        numbers = [f'{n:02}' for n in range(1,11)]
        cases = [
            ('xinzhu_forum_stable_10', '新竹论坛',
             '精英榜252期:[绝杀十码]已公开\n252期:绝杀十码:'+'.'.join(numbers)+':开00准\n上一篇:其他栏目'),
            ('shita_top_10', '大家发(绝杀10码)',
             '大家发(绝杀10码)\n252期:『师太』绝杀十码开:00准\n['+'.'.join(numbers)+']'),
        ]
        for parser, anchor, content in cases:
            target = dict(special_parser=parser, anchor=anchor, region='top', count=10, issue_position_window=5)
            if parser == 'shita_top_10':
                target['stop_anchor'] = '最早发表在'
            doc = SourceDocument(kind='page', url='https://example.test/article/1', content=content,
                                 fingerprint=hashlib.sha256(content.encode()).hexdigest())
            with self.subTest(parser=parser):
                proof = evidence_from_source_document(target,'252',numbers,doc)
                self.assertLess(proof.candidate_start,proof.section_end)
                self.assertEqual(proof.numbers,tuple(numbers))

    def test_dedicated_title_is_evidence_not_unrelated_site_banner(self):
        content = '252期绝杀十码\n252期:绝杀十码【01.02.03.04.05.06.07.08.09.10】'
        target = dict(special_parser='liuhe_bottom_10', region='bottom', count=10,
                      issue_position_window=5, anchor='六合公式-唯一官网')
        doc = SourceDocument(kind='page', url='https://example.test/article/1',
                             content=content, fingerprint=hashlib.sha256(content.encode()).hexdigest())
        proof = evidence_from_source_document(target, '252', [f'{n:02}' for n in range(1, 11)], doc)
        self.assertEqual(proof.scope_kind, 'dedicated_parser')
        self.assertEqual(proof.parser_id, 'liuhe_bottom_10')
        self.assertLessEqual(proof.section_start, proof.candidate_start)
        self.assertLess(proof.candidate_start, proof.section_end)

    def test_wrong_issue_and_direction_remain_rejected(self):
        content = '252期绝杀十码\n252期:绝杀十码【01.02.03.04.05.06.07.08.09.10】'
        doc = SourceDocument(kind='page', url='https://example.test/article/1',
                             content=content, fingerprint=hashlib.sha256(content.encode()).hexdigest())
        for issue, region in [('251', 'bottom'), ('252', 'top')]:
            with self.subTest(issue=issue, region=region), self.assertRaises(ValueError):
                evidence_from_source_document(dict(special_parser='liuhe_bottom_10', region=region,
                    count=10, issue_position_window=5, anchor='六合公式-唯一官网'), issue,
                    [f'{n:02}' for n in range(1,11)], doc)
