import crawler
from kill_numbers.domain import (
    CandidateEvidence,
    CrawlFailure,
    CrawlRequest,
    CrawlResult,
    CrawlStage,
    FailureCode,
    SourceDocument,
    StructuredCrawlError,
    TargetContract,
)


def test_crawler_reexports_domain_result_types():
    assert crawler.CrawlResult is CrawlResult
    assert crawler.CrawlFailure is CrawlFailure
    assert CrawlResult("u", "n", "211", ["01"]).issue == "211"


def test_target_contract_preserves_known_and_extra_fields():
    contract = TargetContract.from_mapping(
        {
            "url": "https://example.test/topic/1",
            "name": "测试站",
            "keywords": ["绝杀十码"],
            "count": 10,
            "region": "top",
            "custom_flag": "kept",
        }
    )

    assert contract.keywords == ("绝杀十码",)
    assert contract.extra == {"custom_flag": "kept"}
    assert contract.to_mapping()["custom_flag"] == "kept"


def test_evidence_models_are_immutable_at_the_boundary():
    request = CrawlRequest(("210", "211"))
    document = SourceDocument(
        kind="landing_api",
        url="https://example.test/api",
        content="211期",
        identity="article-1",
        priority=100,
    )
    candidate = CandidateEvidence(
        issue="211",
        numbers=("01", "02", "03"),
        source_kind=document.kind,
        source_url=document.url,
        document_fingerprint="abc",
    )

    assert request.issues == ("210", "211")
    assert candidate.source_kind == "landing_api"


def test_structured_error_keeps_machine_code_and_chinese_message():
    error = StructuredCrawlError(
        CrawlStage.VALIDATION,
        FailureCode.CANDIDATE_CONFLICT,
        "同期候选冲突",
    )

    assert str(error) == "同期候选冲突"
    assert error.code is FailureCode.CANDIDATE_CONFLICT
