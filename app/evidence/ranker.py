"""Rank classified evidence by scientific usefulness, not retrieval similarity.

Buckets follow the V0.3 brief: DIRECT evidence from a DOI-identified paper
outranks everything, LIMITING evidence is never buried below CONTEXTUAL
evidence just because it contradicts the claim, and a high retrieval score
never lets CONTEXTUAL or UNCERTAIN evidence outrank a lower-scored DIRECT
passage. Within a bucket, the existing retrieval score breaks ties - this is
a transparent categorical ranking, not a fabricated single quality number.

Accepts any object exposing `.evidence_class`, `.doi` and `.retrieval_score`
(e.g. `ClassifiedEvidence`) so this module has no dependency on `models.py`.
"""

from enum import Enum
from typing import Protocol

from app.evidence.classifier import EvidenceClass


class EvidenceTier(str, Enum):
    DIRECT_PRIMARY = "direct_primary"
    LIMITING = "limiting"
    DIRECT_UNVERIFIED = "direct_unverified"
    SECONDARY = "secondary"
    CONTEXTUAL = "contextual"
    UNCERTAIN = "uncertain"


TIER_ORDER = [EvidenceTier.DIRECT_PRIMARY, EvidenceTier.LIMITING, EvidenceTier.DIRECT_UNVERIFIED,
              EvidenceTier.SECONDARY, EvidenceTier.CONTEXTUAL, EvidenceTier.UNCERTAIN]

TIER_LABELS = {
    EvidenceTier.DIRECT_PRIMARY: "Strong direct evidence (DOI-identified primary source)",
    EvidenceTier.LIMITING: "Limiting evidence",
    EvidenceTier.DIRECT_UNVERIFIED: "Direct evidence (source identity not fully verified)",
    EvidenceTier.SECONDARY: "Secondary evidence",
    EvidenceTier.CONTEXTUAL: "Contextual evidence",
    EvidenceTier.UNCERTAIN: "Unresolved / uncertain evidence",
}


class _Rankable(Protocol):
    evidence_class: EvidenceClass
    doi: str | None
    retrieval_score: float


def tier_for(item: _Rankable) -> EvidenceTier:
    """A DOI means the paper's identity is confidently pinned down (Task 1);
    its absence does not make evidence wrong, only harder to verify as primary."""
    if item.evidence_class == EvidenceClass.DIRECT:
        return EvidenceTier.DIRECT_PRIMARY if item.doi else EvidenceTier.DIRECT_UNVERIFIED
    if item.evidence_class == EvidenceClass.LIMITING:
        return EvidenceTier.LIMITING
    if item.evidence_class == EvidenceClass.SECONDARY:
        return EvidenceTier.SECONDARY
    if item.evidence_class == EvidenceClass.CONTEXTUAL:
        return EvidenceTier.CONTEXTUAL
    return EvidenceTier.UNCERTAIN


def rank(items: list) -> list:
    """Stable-sort by tier priority, then by the existing retrieval score."""
    return sorted(items, key=lambda item: (TIER_ORDER.index(tier_for(item)), -item.retrieval_score))
