"""Typed parser failures used to distinguish absence from contract violations."""


class ParserError(ValueError):
    """Base class for deterministic parsing failures."""


class NoCandidateError(ParserError):
    """This independent document does not contain the configured source block."""


class SourceContractError(ParserError):
    """The configured source is present, but its required structure is invalid."""


class CandidateConflictError(SourceContractError):
    """Two or more distinct valid candidates exist where exactly one is required."""


class AmbiguousSourceError(SourceContractError):
    """Multiple complete source scopes produce incompatible results."""
