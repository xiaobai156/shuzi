import crawler
import pytest


NUMBERS = "01.02.03.04.05.06.07.08.09.10"


def issue_row(issue: int) -> str:
    return f"{issue}期绝杀10码 {NUMBERS} 开鼠42准"


def test_windowed_available_issues_scans_the_top_window_once(monkeypatch):
    text = "条纹妇人\n" + "\n".join(issue_row(issue) for issue in range(210, 150, -1))

    def unexpected_extract(*_args, **_kwargs):
        raise AssertionError("窗口检测不应逐期重新调用 extract_issue_numbers")

    monkeypatch.setattr(crawler, "extract_issue_numbers", unexpected_extract)

    assert crawler.detect_available_issues(
        text,
        keywords=["绝杀10码"],
        expected_count=10,
        anchor="条纹妇人",
        region="top",
        issue_position_window=4,
    ) == ["210", "209", "208", "207"]


def test_top_window_does_not_accept_an_old_issue():
    text = "条纹妇人\n" + "\n".join(issue_row(issue) for issue in range(210, 204, -1))

    assert crawler.extract_issue_numbers(
        text,
        ["206"],
        keywords=["绝杀10码"],
        expected_count=10,
        anchor="条纹妇人",
        region="top",
        issue_position_window=4,
    ) == {}


def test_top_window_conflict_still_fails_closed():
    text = "条纹妇人\n" + "\n".join(
        [
            issue_row(210),
            "210期绝杀10码 11.12.13.14.15.16.17.18.19.20 开鼠42准",
            issue_row(209),
            issue_row(208),
        ]
    )

    with pytest.raises(ValueError, match="210期 候选不唯一"):
        crawler.extract_issue_numbers(
            text,
            ["210"],
            keywords=["绝杀10码"],
            expected_count=10,
            anchor="条纹妇人",
            region="top",
            issue_position_window=4,
            strict_ambiguous=True,
        )


def test_direction_window_honors_configured_thirty_rows():
    text = "条纹妇人\n" + "\n".join(issue_row(issue) for issue in range(210, 204, -1))

    assert crawler.extract_issue_numbers(
        text,
        ["207"],
        keywords=["绝杀10码"],
        expected_count=10,
        anchor="条纹妇人",
        region="top",
        issue_position_window=30,
    ) == {"207": NUMBERS.split(".")}
    assert crawler.extract_issue_numbers(
        text,
        ["208"],
        keywords=["绝杀10码"],
        expected_count=10,
        anchor="条纹妇人",
        region="bottom",
        issue_position_window=30,
    ) == {"208": NUMBERS.split(".")}


def test_top_user_page_extracts_once_after_a_forum_page(monkeypatch):
    calls = []

    def fake_fetch_json(url: str):
        if url.endswith("/api/v1/users/123"):
            return {"nickname": "条纹妇人"}
        if url.endswith("/api/v1/users/123/forums"):
            return [
                {"id": "3", "topic": "first", "content": "one"},
                {"id": "2", "topic": "second", "content": "two"},
                {"id": "1", "topic": "third", "content": "three"},
            ]
        if "/api/v1/users/123/forums?lt=" in url:
            return []
        raise AssertionError(f"unexpected URL: {url}")

    def fake_extract(content: str, issues: list[str], **_kwargs):
        calls.append(content)
        return {"210": ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10"]}

    monkeypatch.setattr(crawler, "fetch_json", fake_fetch_json)
    monkeypatch.setattr(crawler, "extract_issue_numbers", fake_extract)

    name, content = crawler.crawl_user_page(
        "https://example.test/#/users/123",
        ["210"],
        keywords=["绝杀10码"],
        expected_count=10,
        anchor="条纹妇人",
        region="top",
        issue_position_window=4,
    )

    assert name == "条纹妇人"
    assert len(calls) == 1
    assert content == "first\none"
