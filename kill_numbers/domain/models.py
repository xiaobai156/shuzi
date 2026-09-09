from dataclasses import dataclass, field
from typing import Any


@dataclass
class CrawlResult:
    url: str
    name: str
    issue: str
    numbers: list[str]
    evidence: "CandidateEvidence | None" = None


@dataclass
class CrawlFailure:
    url: str
    name: str
    reason: str


@dataclass
class RunStats:
    total_targets: int
    initial_success: int
    retry_rescued: int
    retry_passes_used: int
    workers: int


@dataclass
class ManualRiskEntry:
    name: str
    url: str
    reason: str
    status: str
    detail: str


@dataclass(frozen=True)
class CrawlRequest:
    issues: tuple[str, ...]


@dataclass(frozen=True)
class SourceDocument:
    kind: str
    url: str
    content: str
    parent_url: str = ""
    identity: str = ""
    priority: int = 0
    fetched_at: str = ""
    fingerprint: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateEvidence:
    issue: str
    numbers: tuple[str, ...]
    source_kind: str
    source_url: str
    document_fingerprint: str
    anchor: str = ""
    section_start: int = -1
    section_end: int = -1
    candidate_start: int = -1
    scope_kind: str = ""
    source_identity: str = ""
    region: str = ""
    window_size: int = 0
    row_rank: int = -1
    parser_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
