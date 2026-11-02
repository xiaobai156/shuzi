import crawler
from kill_numbers.domain import (
    CandidateEvidence,
    CrawlFailure,
    CrawlResult,
    SourceDocument,
)


def test_crawler_reexports_domain_result_types():
    assert crawler.CrawlResult is CrawlResult
    assert crawler.CrawlFailure is CrawlFailure
    assert CrawlResult("u", "n", "211", ["01"]).issue == "211"


def test_evidence_models_are_immutable_at_the_boundary():
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

    assert candidate.source_kind == "landing_api"
