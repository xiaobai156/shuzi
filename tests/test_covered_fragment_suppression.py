import pytest

import crawler
from kill_numbers.acquisition.documents import make_source_document
from kill_numbers.parsing.errors import CandidateConflictError, SourceContractError


KEYWORD = "\u4e13\u5c5e\u680f\u76ee"
ANCHOR = "\u680f\u76ee\u8d77\u70b9"
STOP = "\u680f\u76ee\u7ec8\u70b9"
ISSUE_MARK = "\u671f"
OPEN_MARK = "\u5f00"


def target(**extra):
    return {
        "url": "https://page.test/topic/1",
        "name": "test-column",
        "keywords": [KEYWORD],
        "count": 3,
        "region": "top",
        "anchor": ANCHOR,
        "stop_anchor": STOP,
        "issue_position_window": 3,
        **extra,
    }


def doc(kind, url, content, priority, *, parent_url="", metadata=None):
    return make_source_document(
        kind=kind,
        url=url,
        parent_url=parent_url,
        content=content,
        priority=priority,
        metadata={"parseable": True, **(metadata or {})},
    )


def row(numbers):
    return f"252{ISSUE_MARK} {KEYWORD} {numbers} {OPEN_MARK}:??"


def complete_page(numbers="01 02 03"):
    script = "https://cdn.test/upload/script/a.js"
    page = doc(
        "rendered_script_page",
        "https://page.test/topic/1",
        f"{ANCHOR}\n{row(numbers)}\n{STOP}",
        110,
        metadata={"script_sources": [script]},
    )
    return page, script


def bad_covered_component(script):
    # A partial fragment can contain a valid anchor/candidate without the later
    # stop marker that exists in the complete reconstructed page.
    return doc(
        "decoded_script_component",
        script + "#decoded-16",
        f"{ANCHOR}\n{row('01 02 03')}",
        75,
        parent_url=script,
    )


def test_complete_reconstruction_is_not_poisoned_by_covered_partial_component():
    page, script = complete_page()
    partial = bad_covered_component(script)
    parsed = crawler.parse_target_document_results([page, partial], target(), ["252"])
    assert parsed.issue_map == {"252": ["01", "02", "03"]}
    assert parsed.source_documents["252"] is page


def test_available_issues_is_not_poisoned_by_covered_partial_component():
    page, script = complete_page()
    partial = bad_covered_component(script)
    available, selected = crawler.available_issues_for_documents([page, partial], target())
    assert available == ["252"]
    assert selected is page


def test_without_complete_reconstruction_partial_component_still_fails_closed():
    partial = bad_covered_component("https://cdn.test/upload/script/a.js")
    with pytest.raises(SourceContractError):
        crawler.parse_target_document_results([partial], target(), ["252"])


def test_unrelated_partial_component_is_not_suppressed():
    page, _script = complete_page()
    other = bad_covered_component("https://cdn.test/upload/script/other.js")
    with pytest.raises(SourceContractError):
        crawler.parse_target_document_results([page, other], target(), ["252"])


def test_independent_conflicting_full_source_still_rejects():
    page, _script = complete_page()
    independent = doc(
        "page",
        "https://independent.test/topic/2",
        f"{ANCHOR}\n{row('04 05 06')}\n{STOP}",
        100,
    )
    with pytest.raises(CandidateConflictError):
        crawler.parse_target_document_results([page, independent], target(), ["252"])


def test_covered_external_script_is_also_redundant_but_independent_inline_is_not():
    page, script = complete_page()
    external = doc(
        "external_script",
        script,
        f"{ANCHOR}\n{row('01 02 03')}",
        70,
        parent_url=page.url,
    )
    parsed = crawler.parse_target_document_results([page, external], target(), ["252"])
    assert parsed.issue_map == {"252": ["01", "02", "03"]}

    inline = doc(
        "inline_script",
        page.url + "#inline-script-1",
        f"{ANCHOR}\n{row('01 02 03')}",
        70,
        parent_url=page.url,
    )
    with pytest.raises(SourceContractError):
        crawler.parse_target_document_results([page, inline], target(), ["252"])
