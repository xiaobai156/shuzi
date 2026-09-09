import hashlib
from contextvars import copy_context
from kill_numbers.acquisition.policy import (
    CURRENT_POLICY,
    allow_discovered_child_host,
    child_url_allowed,
)
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlparse

from kill_numbers.acquisition.discovery import (
    SCRIPT_WORKERS,
    decode_strdecode_payloads,
    iframe_urls,
    script_urls,
)
from kill_numbers.acquisition.http_client import fetch_text
from kill_numbers.domain.models import SourceDocument
from kill_numbers.text_utils import (
    extract_name_from_text,
    remove_fragment,
)


def document_fingerprint(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


def source_identity(url: str) -> str:
    parsed = urlparse(url)
    match = re.search(
        r"/(?:topic|article/(?:admin|manager|lottery))/([^/?#]+)",
        parsed.path,
        re.I,
    )
    if match:
        return match.group(1)
    return parsed.path.rstrip("/").rsplit("/", 1)[-1]


def make_source_document(
    *,
    kind: str,
    url: str,
    content: str,
    parent_url: str = "",
    priority: int,
    identity: str = "",
    metadata: dict | None = None,
) -> SourceDocument:
    return SourceDocument(
        kind=kind,
        url=url,
        parent_url=parent_url,
        identity=identity or source_identity(url),
        content=content,
        priority=priority,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        fingerprint=document_fingerprint(content),
        metadata=dict(metadata or {}),
    )


def visible_html_document(raw_html: str) -> str:
    value = re.sub(r"<script\b[^>]*>.*?</script\s*>", "", raw_html, flags=re.I | re.S)
    value = re.sub(r"<iframe\b[^>]*>.*?</iframe\s*>", "", value, flags=re.I | re.S)
    value = re.sub(r"<(?:style|noscript)\b[^>]*>.*?</(?:style|noscript)\s*>", "", value, flags=re.I | re.S)
    return value


def inline_script_blocks(raw_html: str) -> list[str]:
    values = []
    for match in re.finditer(
        r"<script\b([^>]*)>(.*?)</script\s*>",
        raw_html,
        re.I | re.S,
    ):
        if re.search(r"\bsrc\s*=", match.group(1), re.I):
            continue
        content = match.group(2).strip()
        if content:
            values.append(content)
    return values


def child_page_region(raw_html: str, child_url: str) -> str:
    parsed = urlparse(child_url)
    candidates = [child_url, parsed.path]
    if parsed.query:
        candidates.insert(1, f"{parsed.path}?{parsed.query}")
    candidates.append(parsed.path.rsplit("/", 1)[-1])
    positions = [raw_html.find(value) for value in candidates if value]
    positions = [position for position in positions if position >= 0]
    if not positions:
        return ""
    return "top" if min(positions) < len(raw_html) / 2 else "bottom"


def _embedded_documents(
    raw_html: str,
    page_url: str,
    *,
    parent_url: str,
    visible_priority: int,
    decoded_priority: int,
    inline_priority: int,
    visible_kind: str,
) -> list[SourceDocument]:
    documents = [
        make_source_document(
            kind=visible_kind,
            url=page_url,
            parent_url=parent_url,
            content=visible_html_document(raw_html),
            priority=visible_priority,
            metadata={"parseable": True},
        ),
        make_source_document(
            kind=f"{visible_kind}_source",
            url=page_url,
            parent_url=parent_url,
            content=raw_html,
            priority=0,
            metadata={"parseable": False},
        ),
    ]
    decoded_values = decode_strdecode_payloads(raw_html)
    # Each decoder result is an independent source fragment.  Joining them with
    # whitespace could manufacture a number row that never existed in the page.
    for index, value in enumerate(decoded_values):
        documents.append(
            make_source_document(
                kind="decoded_inline_component",
                url=f"{page_url}#decoded-{index + 1}",
                parent_url=page_url,
                content=value,
                priority=decoded_priority,
                metadata={"parseable": True, "component_index": index},
            )
        )
    for index, value in enumerate(inline_script_blocks(raw_html)):
        documents.append(
            make_source_document(
                kind="inline_script",
                url=f"{page_url}#inline-script-{index + 1}",
                parent_url=page_url,
                content=value,
                priority=inline_priority,
                metadata={"parseable": bool(re.search(r"document\.write(?:ln)?\s*\(", value))},
            )
        )
    return documents


def discover_static_documents(url: str) -> tuple[str, list[SourceDocument]]:
    page_url = remove_fragment(url)
    raw_page = fetch_text(page_url)
    documents = _embedded_documents(
        raw_page,
        page_url,
        parent_url="",
        visible_priority=100,
        decoded_priority=80,
        inline_priority=70,
        visible_kind="page",
    )

    child_requests = [
        ("script", child_url)
        for child_url in script_urls(raw_page, page_url)
    ]
    child_requests.extend(
        ("iframe", child_url)
        for child_url in iframe_urls(raw_page, page_url)
    )

    def fetch_child(kind_and_url: tuple[str, str]) -> tuple[str, str, str]:
        kind, child_url = kind_and_url
        if kind == "script" and not child_url_allowed(child_url, page_url):
            with allow_discovered_child_host(child_url):
                return kind, child_url, fetch_text(child_url)
        return kind, child_url, fetch_text(child_url)

    child_results: dict[tuple[str, str], str] = {}
    if child_requests:
        workers = min(max(SCRIPT_WORKERS, 1), len(child_requests))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(copy_context().run, fetch_child, request): request
                for request in child_requests
            }
            for future in as_completed(future_map):
                try:
                    kind, child_url, content = future.result()
                except Exception:
                    continue
                child_results[(kind, child_url)] = content

    for kind, child_url in child_requests:
        content = child_results.get((kind, child_url))
        if content is None:
            continue
        page_region = child_page_region(raw_page, child_url)
        if kind == "script":
            documents.append(
                make_source_document(
                    kind="external_script",
                    url=child_url,
                    parent_url=page_url,
                    content=content,
                    priority=70,
                    metadata={"parseable": bool(re.search(r"document\.write(?:ln)?\s*\(", content))
                        or bool(CURRENT_POLICY.get().source_url_pattern and re.search(CURRENT_POLICY.get().source_url_pattern, child_url, re.I)),
                        "page_region": page_region},
                )
            )
            decoded_values = decode_strdecode_payloads(content)
            for index, decoded in enumerate(decoded_values):
                documents.append(
                    make_source_document(
                        kind="decoded_script_component",
                        url=f"{child_url}#decoded-{index + 1}",
                        parent_url=child_url,
                        content=decoded,
                        priority=75,
                        metadata={
                            "parseable": True,
                            "component_index": index,
                            "page_region": page_region,
                        },
                    )
                )
            continue

        documents.extend(
            _embedded_documents(
                content,
                child_url,
                parent_url=page_url,
                visible_priority=90,
                decoded_priority=65,
                inline_priority=60,
                visible_kind="iframe",
            )
        )

    name = extract_name_from_text(
        documents[0].content,
        fallback=urlparse(url).netloc,
    )
    return name, documents


def parseable_documents(documents: list[SourceDocument]) -> list[SourceDocument]:
    return [
        document
        for document in documents
        if document.metadata.get("parseable", True) and document.content.strip()
    ]


def document_debug_text(documents: list[SourceDocument]) -> str:
    parts = []
    for document in documents:
        parts.extend(
            [
                (
                    f"===== DOCUMENT kind={document.kind} priority={document.priority} "
                    f"url={document.url} fingerprint={document.fingerprint} ====="
                ),
                document.content,
            ]
        )
    return "\n".join(parts)
