import unittest
from kill_numbers.parsing.registry import parse_target_content


class DomBoundaryTests(unittest.TestCase):
    target = dict(anchor='作者甲', stop_anchor=None, content_class='topic-content',
                  count=3, keywords=['杀三码'], region='top', issue_position_window=1)
    row = '<p>252期杀三码【01.02.03】</p>'

    def test_closing_container_excludes_other_column(self):
        html = '作者甲<div class="topic-content">'+self.row+'</div><p>252期杀三码【04.05.06】</p>'
        self.assertEqual(parse_target_content(html, self.target, ['252']), {'252':['01','02','03']})

    def test_rows_outside_container_never_enter_direction_window(self):
        wrong = '<p>252期杀三码【04.05.06】</p>'
        html = '作者甲' + wrong + '<div class="topic-content">' + self.row + '</div>' + wrong
        for region in ['top', 'bottom']:
            with self.subTest(region=region):
                self.assertEqual(parse_target_content(html, {**self.target,'region':region}, ['252']),
                                 {'252':['01','02','03']})

    def test_nested_div_and_windows_line_endings(self):
        for newline in ['\r\n','\r\r\n','\n']:
            html = '作者甲' + newline + '<div class="topic-content"><div>' + self.row + '</div></div>'
            self.assertEqual(parse_target_content(html,self.target,['252']), {'252':['01','02','03']})

    def test_missing_duplicate_or_unclosed_container_is_failure(self):
        for html in [self.row, '作者甲<div class="topic-content">'+self.row,
                     '作者甲'+('<div class="topic-content">'+self.row+'</div>')*2]:
            with self.subTest(html=html), self.assertRaises(ValueError):
                parse_target_content(html, self.target, ['252'])

    def test_repeated_identity_inside_unique_container_does_not_make_scope_ambiguous(self):
        html = (
            '作者甲<div class="topic-content">'
            + self.row +
            '<p>251期杀三码【04.05.06】作者甲</p>'
            '<p>作者甲</p></div>'
        )
        self.assertEqual(
            parse_target_content(html, self.target, ['252']),
            {'252':['01','02','03']},
        )

    def test_repeated_identity_before_unique_container_is_allowed(self):
        html = '主题作者甲 作者甲<div class="topic-content">' + self.row + '</div>'
        self.assertEqual(
            parse_target_content(html, self.target, ['252']),
            {'252':['01','02','03']},
        )

    def test_identity_only_inside_container_is_failure(self):
        html = '<div class="topic-content"><p>作者甲</p>' + self.row + '</div>'
        with self.assertRaises(ValueError):
            parse_target_content(html, self.target, ['252'])

    def test_wrong_anchor_and_outside_window(self):
        with self.assertRaises(ValueError):
            parse_target_content('作者乙<div class="topic-content">'+self.row+'</div>',self.target,['252'])
        html='作者甲<div class="topic-content">'+self.row.replace('252','253')+self.row+'</div>'
        self.assertEqual(parse_target_content(html,self.target,['252']), {})
