from dataclasses import dataclass
from enum import Enum


class CrawlStage(str, Enum):
    CONFIG = "config"
    ACQUISITION = "acquisition"
    DISCOVERY = "discovery"
    PARSING = "parsing"
    VALIDATION = "validation"
    STORAGE = "storage"


class FailureCode(str, Enum):
    NETWORK_TIMEOUT = "network_timeout"
    SSL_FAILED = "ssl_failed"
    HTTP_FAILED = "http_failed"
    CONTENT_NOT_PUBLISHED = "content_not_published"
    STRUCTURE_CHANGED = "structure_changed"
    ANCHOR_MISSING = "anchor_missing"
    ISSUE_MISSING = "issue_missing"
    IDENTITY_MISMATCH = "identity_mismatch"
    COUNT_INVALID = "count_invalid"
    DUPLICATE_NUMBERS = "duplicate_numbers"
    CANDIDATE_CONFLICT = "candidate_conflict"
    BROWSER_FAILED = "browser_failed"
    STORAGE_FAILED = "storage_failed"


@dataclass
class StructuredCrawlError(Exception):
    stage: CrawlStage
    code: FailureCode
    message: str
    source_url: str = ""

    def __str__(self) -> str:
        return self.message

