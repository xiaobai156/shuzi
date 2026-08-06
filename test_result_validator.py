import pytest

import crawler
from kill_numbers.acquisition.documents import make_source_document
from kill_numbers.domain.models import CrawlResult
from kill_numbers.validation.result_validator import (
    evidence_from_source_document,
    validate_crawl_results,
    validate_issue_map,
)


TARGET = {
    "url": "https://example.test/topic/1",
    "name": "测试站",
    "count": 4,
}


def result_evidence(issue: str, numbers: list[str]):
    document = make_source_document(
        kind="page",
        url=TARGET["url"],
        content=f"{issue}期 {' '.join(numbers)}",
        priority=100,
    )
    return evidence_from_source_document(TARGET, issue, numbers, document)


def test_issue_map_validation_preserves_original_number_order():
    found = validate_issue_map(
        TARGET,
        ["211"],
        {"211": ["04", "01", "03", "02"]},
    )

    assert found == {"211": ["04", "01", "03", "02"]}


def test_source_evidence_keeps_anchor_and_candidate_offsets():
    evidence = result_evidence("211", ["01", "02", "03", "04"])

    assert evidence.anchor == ""
    assert evidence.section_start == 0
    assert evidence.section_end > evidence.section_start
    assert evidence.candidate_start >= evidence.section_start


def test_issue_map_validation_requires_every_requested_issue():
    with pytest.raises(ValueError, match="缺少指定期数 210期"):
        validate_issue_map(
            TARGET,
            ["210", "211"],
            {"211": ["04", "01", "03", "02"]},
        )


@pytest.mark.parametrize(
    ("issue_map", "message"),
    [
        ({"210": ["01", "02", "03", "04"]}, "混入非指定期数"),
        ({"211": ["01", "02", "03"]}, "号码数量 3"),
        ({"211": ["01", "01", "03", "04"]}, "号码有重复"),
        ({"211": ["01", "02", "03", "50"]}, "超出01至49"),
    ],
)
def test_issue_map_validation_fails_closed(issue_map, message):
    with pytest.raises(ValueError, match=message):
        validate_issue_map(TARGET, ["211"], issue_map)


def test_source_document_must_match_dedicated_url_contract():
    target = {**TARGET, "source_url_pattern": "/wanted\\.js"}
    document = make_source_document(
        kind="external_script",
        url="https://example.test/other.js",
        content="211期 01 02 03 04",
        priority=100,
    )

    with pytest.raises(ValueError, match="专属 URL 契约"):
        validate_issue_map(
            target,
            ["211"],
            {"211": ["01", "02", "03", "04"]},
            source_document=document,
        )


def test_result_validation_rejects_same_issue_conflict():
    results = [
        CrawlResult(
            TARGET["url"],
            TARGET["name"],
            "211",
            ["01", "02", "03", "04"],
            result_evidence("211", ["01", "02", "03", "04"]),
        ),
        CrawlResult(
            TARGET["url"],
            TARGET["name"],
            "211",
            ["05", "06", "07", "08"],
            result_evidence("211", ["05", "06", "07", "08"]),
        ),
    ]

    accepted, reason = validate_crawl_results(TARGET, ["211"], results)

    assert accepted == []
    assert "211期同期候选冲突" in reason


def test_result_validation_rejects_missing_source_evidence():
    accepted, reason = validate_crawl_results(
        TARGET,
        ["211"],
        [CrawlResult(TARGET["url"], TARGET["name"], "211", ["01", "02", "03", "04"])],
    )

    assert accepted == []
    assert "211期来源证据缺失" in reason


def test_crawler_uses_the_only_formal_issue_map_validator():
    assert crawler.validate_issue_map is validate_issue_map
