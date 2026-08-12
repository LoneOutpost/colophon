"""Extract the title token(s) from an identity source (folder name / filename) by tokenizing it and
removing the tokens that play OTHER roles — a known author or series (matched with normalize_key, which
folds initials/punctuation/PascalCase so 'HP Lovecraft' == 'H. P. Lovecraft'), a `Bk N`/`Book N`/`Vol
N` marker, and structural/index/bracketed tokens. General and structural: roles are identified by
cross-reference and markers, never by a corpus-specific encoding suffix. See
tasks/2026-08-12-title-after-author-design.md."""

from __future__ import annotations

import re

from colophon.core.cohort_constancy import _tokens
from colophon.core.metadata_quality import is_structural_marker
from colophon.core.normalize import normalize_key

_SERIES_MARKER = re.compile(r"\b(?:bk|book|vol|volume)\s*\d", re.IGNORECASE)


def title_candidates(name: str, *, authors: list[str], series: list[str]) -> list[str]:
    """The title token(s) in `name`: its ` - ` / `.-.` segments minus tokens playing other roles.

    Author/series removal MAY return [] (an author-only folder has no title here); structural removal
    is skipped when it would remove the last token, so a bare-number title like "1984" survives."""
    akeys = {normalize_key(a) for a in authors}
    skeys = {normalize_key(s) for s in series}
    kept = [
        t for t in _tokens(name)
        if not t.startswith(("(", "["))
        and normalize_key(t) not in akeys and normalize_key(t) not in skeys
        and not _SERIES_MARKER.search(t)
    ]
    if not kept:
        return []
    non_structural = [t for t in kept if not is_structural_marker(t)]
    return non_structural if non_structural else kept
