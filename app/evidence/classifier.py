"""Deterministic evidence-relationship classification.

No LLM is involved: the application has no local chat model (only a sentence
embedder used for semantic retrieval), and citation/claim classification must
stay conservative, so this module uses fixed vocabularies and regular
expressions instead of inference. `classify_passage` never edits or rewrites
the passage text; it only reports which cues fired and how confident the
classification itself is (not whether the underlying science is true).
"""

from dataclasses import dataclass
from enum import Enum
import re
import unicodedata


class EvidenceClass(str, Enum):
    DIRECT = "DIRECT"
    SECONDARY = "SECONDARY"
    CONTEXTUAL = "CONTEXTUAL"
    LIMITING = "LIMITING"
    UNCERTAIN = "UNCERTAIN"


# Stimulus vocabulary groups terms that describe the same physical cause, so a
# claim about "strain" is not matched against a passage about "current" merely
# because both mention the same material and a phase transition.
STIMULUS_CATEGORIES: dict[str, tuple[str, ...]] = {
    "mechanical": ("strain", "strained", "mechanical strain", "wrinkle", "wrinkling", "wrinkled",
                   "curvature", "curved", "bending", "bend", "bent", "flexural", "external strain",
                   "compressive strain", "tensile strain", "tensile stress", "stress-induced", "deformation"),
    "electrical": ("current", "voltage", "electric field", "electrical bias", "gate voltage",
                   "field-induced", "current-induced", "electric-field-induced", "applied bias"),
    "thermal": ("temperature", "thermal", "heating", "annealing", "thermally induced"),
    "optical": ("laser", "photoexcitation", "photo-induced", "illumination", "optical excitation"),
    "chemical": ("doping", "intercalation", "chemically induced"),
}

PHASE_REGEX = re.compile(
    r"phase\s*(transition|change|transformation)"
    r"|structural\s*(transition|transformation|phase)"
    r"|polymorphic\s*transition"
    r"|\bβ′?\s*(→|->|to)\s*α\b"
    r"|\bα\s*(→|->|to)\s*β′?\b",
    re.IGNORECASE,
)

LIMIT_CUES = re.compile(
    r"negligible\s+(effect|influence|impact|role)"
    r"|no\s+significant\s+(effect|influence|impact|change)"
    r"|(does|do)\s+not\s+(significantly\s+)?(affect|influence|induce|trigger)"
    r"|fails?\s+to\s+(induce|trigger|cause)"
    r"|insufficient\s+to\s+(induce|trigger|cause)"
    r"|weak(ly)?\s+influence"
    r"|unaffected\s+by"
    r"|no\s+observable\s+(effect|change)"
    r"|(has|have|exhibits?)\s+(a\s+)?negligible\s+effect"
    r"|minimal\s+(effect|impact|influence|role)",
    re.IGNORECASE,
)

SECONDARY_CUES = re.compile(
    r"\bet\s+al\.?\s+(reported?|reports?|showed|shows?|demonstrated|demonstrates?|found|observed|proposed)\b"
    r"|\bhas\s+been\s+reported\b"
    r"|\bhave\s+been\s+reported\b"
    r"|\bwas\s+reported\b"
    r"|\bwere\s+reported\b"
    r"|\bprevious(ly)?\s+(studies|work|reports?|research)\b"
    r"|\bref\.?\s*\[?\d+\]?\s*(reported|showed|demonstrated)\b"
    r"|\bit\s+has\s+been\s+shown\b"
    r"|\breported\s+that\b"
    r"|\bhave\s+shown\s+that\b"
    r"|\bhas\s+shown\s+that\b",
    re.IGNORECASE,
)

DIRECT_VOICE_CUES = re.compile(
    r"\bwe\s+(show|report|demonstrate|find|observe|reveal)\b"
    r"|\b(this|the present)\s+(work|study|paper)\s+(shows|demonstrates|reports)\b"
    r"|\bour\s+(results?|data|findings?)\s+(show|reveal|demonstrate)\b"
    r"|\benables?\b"
    r"|\binduc(e|es|ed|ing)\b"
    r"|\bdrive?s?\b"
    r"|\btrigger(s|ed|ing)?\b"
    r"|\bresults?\s+in\b"
    r"|\bleads?\s+to\b"
    r"|\bcauses?\b",
    re.IGNORECASE,
)

# Generic chemical-formula-like token, e.g. In2Se3, InSe, MoS2, GaN.
MATERIAL_TOKEN_RE = re.compile(r"\b[A-Z][a-z]?\d*(?:[A-Z][a-z]?\d*){1,3}\b")


@dataclass(frozen=True)
class ClassificationResult:
    evidence_class: EvidenceClass
    evidence_confidence: float
    rationale: str
    claim_match: bool
    stimulus_match: bool
    material_match: bool
    phase_match: bool
    source_type: str


def _normalize(text: str) -> str:
    """NFKC-normalize before cue matching only, so PDF-extracted typographic
    ligatures (e.g. "eﬀect", "inﬂuence") don't silently fail the plain-ASCII
    cue regexes below; the caller's original passage text is never altered."""
    return unicodedata.normalize("NFKC", text)


def _matched_categories(text: str) -> set[str]:
    lowered = text.lower()
    return {category for category, terms in STIMULUS_CATEGORIES.items()
            if any(term in lowered for term in terms)}


def _material_tokens(text: str) -> set[str]:
    return {match.casefold() for match in MATERIAL_TOKEN_RE.findall(text)}


def classify_passage(claim: str, passage_text: str, paper_title: str = "") -> ClassificationResult:
    """Classify one passage's evidential relationship to `claim`.

    Each of material/phase/stimulus is only enforced as a constraint when the
    claim itself asserts it; an unconstrained dimension is treated as
    satisfied so a generic claim is not forced into UNCERTAIN.
    """
    claim_norm = _normalize(claim)
    passage_norm = _normalize(passage_text)

    claim_stimulus = _matched_categories(claim_norm)
    passage_stimulus = _matched_categories(passage_norm)
    stimulus_match = bool(claim_stimulus & passage_stimulus) if claim_stimulus else True

    claim_materials = _material_tokens(claim_norm)
    haystack = f"{passage_norm} {_normalize(paper_title)}"
    passage_materials = _material_tokens(haystack)
    matched_materials = claim_materials & passage_materials
    material_match = bool(matched_materials) if claim_materials else True

    claim_asks_phase = bool(PHASE_REGEX.search(claim_norm))
    passage_has_phase = bool(PHASE_REGEX.search(passage_norm))
    phase_match = passage_has_phase if claim_asks_phase else True

    is_secondary = bool(SECONDARY_CUES.search(passage_norm))
    is_limiting = bool(LIMIT_CUES.search(passage_norm))
    is_direct_voice = bool(DIRECT_VOICE_CUES.search(passage_norm))

    has_citation = bool(re.search(r"\[\d+", passage_norm))
    claim_match = material_match and phase_match

    if is_secondary:
        evidence_class = EvidenceClass.SECONDARY
        confidence = 0.75 if has_citation else 0.6
        rationale = ("Passage attributes the finding to another study (attribution language such as "
                     "'et al. reported' / 'has been reported' detected); treated as secondary evidence "
                     "pending primary-source verification.")
        source_type = "secondary_report"
    elif is_limiting and claim_match and (not claim_stimulus or stimulus_match):
        evidence_class = EvidenceClass.LIMITING
        confidence = min(0.9, 0.7 + 0.1 * material_match + 0.1 * phase_match)
        rationale = ("Passage reports a negative/negligible result or boundary condition for the same "
                     "material and phase-transition topic as the claim; this limits generalizing the claim.")
        source_type = "experimental_or_primary_voice" if is_direct_voice else "unspecified"
    elif claim_match and stimulus_match:
        evidence_class = EvidenceClass.DIRECT
        confidence = min(0.95, 0.6 + 0.1 * material_match + 0.1 * phase_match + 0.1 * stimulus_match
                          + (0.05 if is_direct_voice else 0.0))
        matched_terms = ", ".join(sorted(matched_materials)) or "paper title/topic"
        rationale = (f"Passage discusses the same material ({matched_terms}), the same phase-transition "
                     f"language, and the same stimulus category as the claim, and reports this as its own "
                     f"finding rather than attributing it to another study.")
        source_type = "experimental_or_primary_voice" if is_direct_voice else "unspecified"
    elif claim_match:
        evidence_class = EvidenceClass.CONTEXTUAL
        confidence = 0.55
        rationale = ("Passage discusses the same material and phase-transition topic as the claim, but the "
                     "stimulus it studies does not match the stimulus named in the claim.")
        source_type = "unspecified"
    elif material_match or phase_match:
        evidence_class = EvidenceClass.CONTEXTUAL
        confidence = 0.4
        rationale = "Passage only partially overlaps the claim's material or phase-transition topic."
        source_type = "unspecified"
    else:
        evidence_class = EvidenceClass.UNCERTAIN
        confidence = 0.3
        rationale = ("Not enough of the claim's material, phase-transition, or stimulus terms were found in "
                     "this passage to reliably determine its evidential relationship to the claim.")
        source_type = "unspecified"

    return ClassificationResult(evidence_class, round(confidence, 2), rationale, claim_match,
                                 stimulus_match, material_match, phase_match, source_type)
