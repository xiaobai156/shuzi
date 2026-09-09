from html.parser import HTMLParser

from kill_numbers.parsing.common import scope_text_by_anchor_with_offset
from kill_numbers.text_utils import html_to_text


def content_section(content: str, target: dict) -> tuple[str, int]:
    """Use an explicitly configured, unique and closed div; never a page-tail fallback."""
    class_name = target['content_class']
    if class_name not in {'content', 'topic-content', 'd-content'}:
        raise ValueError('正文容器配置无效')
    lines = content.split('\n')
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)

    class Boundaries(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.matches = []

        def handle_starttag(self, tag, attrs):
            if tag == 'div':
                matched = class_name in dict(attrs).get('class', '').split()
                self.stack.append(self.position() if matched else None)

        def handle_endtag(self, tag):
            if tag == 'div' and self.stack:
                start = self.stack.pop()
                if start is not None:
                    self.matches.append((start, self.position()))

        def position(self):
            line, column = self.getpos()
            return offsets[line - 1] + column

    parser = Boundaries()
    parser.feed(content)
    parser.close()
    if len(parser.matches) != 1 or any(start is not None for start in parser.stack):
        raise ValueError('正文容器缺失、未闭合或不唯一')
    raw_start, raw_end = parser.matches[0]
    full_text = html_to_text(content)
    prefix = html_to_text(content[:raw_end])
    if not full_text.startswith(prefix):
        raise ValueError('正文容器文本偏移不一致')
    _, anchor_start = scope_text_by_anchor_with_offset(prefix, target.get('anchor'))
    body = html_to_text(content[raw_start:raw_end])
    if not body or not prefix.endswith(body):
        raise ValueError('正文容器文本偏移不一致')
    start = len(prefix) - len(body)
    if anchor_start > start:
        raise ValueError('正文身份锚点必须位于该栏目标题或作者区')
    return body, start
