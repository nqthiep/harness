"""A threshold from labelled pairs — or the refusal to hand one over.

Shipped rather than copied, by this package's own four admission criteria
(`harness/contrib/__init__.py`), applied for the second time and to code I had just
written in the wrong tier (ADR-101):

1. **Domain-neutral.** It reads two lists of similarity scores. Nothing here knows what
   was compared — faces, documents, audio fingerprints, two builds of a model.
2. **No new dependency.** Pure arithmetic over two sequences.
3. **A safety argument, which is the whole point.** It exists to REFUSE. A threshold
   nobody measured is how an identity system decides who somebody is; a threshold
   measured on three pairs is worse, because it comes with a number.
4. **Not a seam.** It is a function, not a protocol anything is written against.

It grew in `examples/vision_tools.py` next to `align_landmarks`, which stays there — that
one knows what an eye corner is, so it fails criterion 1. The line runs between them, not
around the file.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


#: How much evidence a threshold needs before `IdentityLedger.from_calibration` will
#: build on it. Both are judgments, stated as constants so they can be argued with
#: instead of being buried in a comparison.
#:
#: Ten pairs a side: with fewer, one mislabelled or unlucky pair moves the threshold by
#: more than the gap it sits in, so the number would be a property of the sample rather
#: than of the embedder.
#:
#: A gap of 0.05: measured on real embeddings, widening a crop box by 20% on the
#: IDENTICAL face moved cosine similarity by 0.36 (1.0000 -> 0.6414). A gap thinner than
#: 0.05 will not survive the next crop, so it is not a separation, it is a coincidence.
#: Both gates exist because a real experiment walked through the `separable` check: the
#: landmark-geometry variant measured on FULL frames produced a gap of 0.0083 across
#: three pairs a side, drawn from two people, and read as "separable" (ADR-095). The
#: same feature measured through the code this library actually ships does not separate
#: at all — which is the other half of that lesson.
MIN_CALIBRATION_PAIRS = 10

MIN_CALIBRATION_GAP = 0.05

#: The ambiguity band `Calibration` carries for its caller: two candidates closer together
#: than this are not distinguishable by this measurement, so the honest answer is "I don't
#: know" rather than the higher number.
#:
#: Defaulted to `MIN_CALIBRATION_GAP` and justified by it, which is the only way to pick
#: this number without knowing the domain: a difference finer than the gap the threshold
#: itself needs is finer than this measurement can see. The domain-specific reasoning —
#: why a confident wrong NAME is so much worse than an admitted gap — belongs with the
#: caller, and `examples/vision_tools.DEFAULT_MARGIN` keeps it (ADR-101).
DEFAULT_AMBIGUITY_MARGIN = MIN_CALIBRATION_GAP

@dataclass(frozen=True)
class Calibration:
    """What a threshold is allowed to be, given labelled pairs you actually measured.

    The point is not the number it returns — it is `separable`. A threshold is only
    meaningful when every same-person pair scores ABOVE every different-person pair; when
    they overlap, no threshold anywhere separates them and the honest output is "this
    embedder cannot do this", not a number that fails quietly on the pairs in between.

    Two summary statistics rather than a full ROC curve: at the sample sizes a person can
    realistically label by hand (tens of pairs, not thousands), the worst same-person
    score and the best different-person score are the only two numbers a threshold can be
    derived from, and a curve fitted through 20 points would look far more authoritative
    than it is — the same reasoning `eval/cost.py` uses for reporting a Wilson interval
    instead of a bare rate.
    """

    threshold: float | None
    margin: float
    worst_same: float
    best_different: float
    n_same: int
    n_different: int
    min_pairs: int = MIN_CALIBRATION_PAIRS
    min_gap: float = MIN_CALIBRATION_GAP

    @property
    def separable(self) -> bool:
        """Whether ANY threshold separates this sample. Necessary, not sufficient — see
        `enough_evidence`, which is the question a caller actually wants answered."""
        return self.threshold is not None

    @property
    def enough_evidence(self) -> bool:
        """Whether the separation is worth trusting: separable, on enough pairs, by
        enough margin. A three-pair sample separating by 0.0083 is `separable` and is
        not this."""
        return (self.separable
                and min(self.n_same, self.n_different) >= self.min_pairs
                and self.gap >= self.min_gap)

    @property
    def complaint(self) -> str:
        """Why `enough_evidence` is false, in one clause. `""` when it is true."""
        if not self.separable:
            return (f"the distributions overlap by {-self.gap:.4f}, so no threshold "
                    f"works at all")
        if min(self.n_same, self.n_different) < self.min_pairs:
            return (f"only {min(self.n_same, self.n_different)} pairs on the thinner "
                    f"side, and {self.min_pairs} are needed before the threshold is a "
                    f"property of the embedder rather than of the sample")
        if self.gap < self.min_gap:
            return (f"the gap is {self.gap:+.4f}, under the {self.min_gap} a crop change "
                    f"alone can move a score by")
        return ""

    @property
    def gap(self) -> float:
        """How much room there is between the two distributions. Negative when they
        overlap, and the size of the overlap is how badly."""
        return self.worst_same - self.best_different

    def __str__(self) -> str:
        head = (f"{self.n_same} same-person pairs (worst {self.worst_same:.4f}), "
                f"{self.n_different} different-person pairs "
                f"(best {self.best_different:.4f})")
        if self.enough_evidence:
            assert self.threshold is not None
            return (f"{head}\n  threshold {self.threshold:.4f}, gap {self.gap:+.4f} — "
                    f"USABLE on this sample")
        if not self.separable:
            return (f"{head}\n  NOT separable: {self.complaint}. Change the EMBEDDER, "
                    f"not the number — a generic image embedder encodes the picture, "
                    f"not the person (see DEFAULT_THRESHOLD).")
        assert self.threshold is not None
        return (f"{head}\n  separable at {self.threshold:.4f}, gap {self.gap:+.4f}, but "
                f"NOT ENOUGH EVIDENCE: {self.complaint}.")

def calibrate(same: Sequence[float], different: Sequence[float], *,
              margin: float = DEFAULT_AMBIGUITY_MARGIN,
              min_pairs: int = MIN_CALIBRATION_PAIRS,
              min_gap: float = MIN_CALIBRATION_GAP) -> Calibration:
    """A threshold from labelled cosine scores, or the refusal to give one.

    `same` are scores between two embeddings of the SAME person, `different` between two
    people. Both must be non-empty: a threshold derived from one side alone is a threshold
    that has never seen the error it is supposed to prevent.

    The threshold lands at the midpoint of the gap, not at `worst_same`: sitting exactly
    on the worst observed same-person pair guarantees the next slightly-worse one is
    rejected, and the midpoint is the only choice that gives both error directions the
    same room on the evidence available.
    """
    if not same or not different:
        raise ValueError(
            "calibrate needs BOTH same-person and different-person pairs — a threshold "
            "fitted to one side has never seen the error it exists to prevent")
    worst_same, best_different = min(same), max(different)
    threshold = ((worst_same + best_different) / 2.0
                 if worst_same > best_different else None)
    return Calibration(threshold, margin, worst_same, best_different,
                       len(same), len(different), min_pairs, min_gap)
