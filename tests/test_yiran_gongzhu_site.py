import hashlib
import json

import crawler
from kill_numbers.domain.models import SourceDocument
from kill_numbers.validation.result_validator import evidence_from_source_document


PARTS = ["依然公主尽位杀码", "依然公主首位杀码"]
TARGET = {
    "url": "http://www.2018512.com/read.php?tid=15&fpage=1&page=3",
    "name": "依然公主",
    "keywords": PARTS,
    "count": 6,
    "region": "bottom",
    "anchor": PARTS[0],
    "part_anchors": PARTS,
    "issue_position_window": 10,
    "special_parser": "yiran_gongzhu_bottom_6",
}


def page_fixture() -> str:
    return """
    <div class='read_48'>
      【264期：依然公主尽位杀码：21,23,24,开21准】
      【265期：依然公主尽位杀码：21,20,22,开49准】
      【266期：依然公主尽位杀码：21,20,22,开00准】
      【264期：依然公主首位杀码：01,02,03,开21准】
      【265期：依然公主首位杀码：05,02,49,开49错】
      【266期：依然公主首位杀码：07,10,49,开00准】
    </div>
    """


def test_yiran_gongzhu_merges_both_bottom_columns_in_order():
    assert crawler.parse_target_content(page_fixture(), TARGET, ["266"]) == {
        "266": ["21", "20", "22", "07", "10", "49"]
    }


def test_yiran_gongzhu_requires_both_columns():
    incomplete = page_fixture().replace("依然公主首位杀码：07,10,49,开00准", "")
    assert crawler.parse_target_content(incomplete, TARGET, ["266"]) == {}


def test_yiran_gongzhu_evidence_covers_the_merged_rows():
    content = page_fixture()
    document = SourceDocument(
        kind="page",
        url=TARGET["url"],
        content=content,
        fingerprint=hashlib.sha256(content.encode()).hexdigest(),
    )
    evidence = evidence_from_source_document(
        TARGET, "266", ["21", "20", "22", "07", "10", "49"], document
    )
    assert evidence.anchor == "依然公主尽位杀码 + 依然公主首位杀码"
    assert evidence.section_start <= evidence.candidate_start < evidence.section_end


def test_yiran_gongzhu_target_contract_is_accepted(tmp_path):
    path = tmp_path / "targets.json"
    path.write_text(json.dumps([TARGET], ensure_ascii=False), encoding="utf-8")
    assert crawler.load_targets(path)[0]["name"] == "依然公主"
