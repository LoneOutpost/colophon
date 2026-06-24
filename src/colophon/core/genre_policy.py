"""Configurable genre policy: synonym mapping + optional accepted-genres
whitelist. A pure value-object applied where genres are normalized."""

from __future__ import annotations

from colophon.core.models import _Base
from colophon.core.normalize import dedupe_normalized


class GenrePolicy(_Base):
    """Canonicalize a list of genres: map synonyms to a preferred spelling and,
    when enabled, drop genres not on the accepted list."""

    mapping: dict[str, str] = {}  # noqa: RUF012 - pydantic default, copied per instance
    accepted: list[str] = []  # noqa: RUF012 - pydantic default, copied per instance
    whitelist_enabled: bool = False

    def canonicalize(self, genres: list[str]) -> list[str]:
        """Map each raw genre (case-insensitive key) to its canonical spelling,
        title-case via normalize_text, drop blanks, filter to the accepted set
        (only when whitelist_enabled and accepted is non-empty, case-insensitive),
        and dedupe case-insensitively preserving first-seen order."""
        lower_map = {k.strip().casefold(): v for k, v in self.mapping.items()}
        accepted_fold = {a.strip().casefold() for a in self.accepted if a.strip()}
        filtering = self.whitelist_enabled and bool(accepted_fold)

        def _mapped(raw: str) -> str:
            return lower_map.get((raw or "").strip().casefold(), raw)

        def _accepted(fold: str) -> bool:
            return fold in accepted_fold

        return dedupe_normalized(genres, transform=_mapped, keep=_accepted if filtering else None)
