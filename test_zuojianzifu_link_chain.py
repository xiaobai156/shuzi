import crawler
from kill_numbers.acquisition.documents import make_source_document


TARGET = {
    "url": "https://example.test/listing.html",
    "name": "作茧自缚",
    "keywords": ["杀特十码"],
    "count": 10,
    "region": "top",
    "anchor": "作茧自缚",
    "issue_position_window": 3,
    "special_parser": "zuojianzifu_link_chain",
}


def source_document(url: str, content: str, *, page_region: str = ""):
    metadata = {"parseable": True}
    if page_region:
        metadata["page_region"] = page_region
    return make_source_document(
        kind="decoded_script_stream",
        url=url,
        content=content,
        priority=75,
        metadata=metadata,
    )


def test_link_chain_preserves_selected_article_document_for_validation(monkeypatch):
    listing_url = TARGET["url"]
    article_url = "https://example.test/article.html"
    documents = {
        listing_url: [
            source_document(
                listing_url,
                '<a href="/article.html"><p>211期：【作茧自缚】☆杀特十码☆实九见证!</p></a>',
            )
        ],
        article_url: [
            source_document(
                article_url,
                "作茧自缚\n"
                "211期 杀特十码 25 31 08 32 07 13 44 19 37 20\n"
                "210期 杀特十码 01 02 03 04 05 06 07 08 09 10\n"
                "209期 杀特十码 11 12 13 14 15 16 17 18 19 20\n"
                "208期 杀特十码 21 22 23 24 25 26 27 28 29 30\n",
                page_region="top",
            )
        ],
    }

    def fake_discover(url: str):
        return "测试页面", documents[url]

    monkeypatch.setattr(crawler, "discover_static_documents", fake_discover)

    results, failure = crawler.crawl_one(TARGET, ["211"])

    assert failure is None
    assert len(results) == 1
    assert results[0].numbers == ["25", "31", "08", "32", "07", "13", "44", "19", "37", "20"]
    assert results[0].evidence is not None
    assert results[0].evidence.source_url == article_url
    assert results[0].evidence.document_fingerprint == documents[article_url][0].fingerprint
