"""One canonical sequence marker for the whole pipeline: every wild form a filename uses to say
"which piece" is parsed into a single `SeqMarker` the sequencer, grouping, and identity all key off,
instead of each re-deriving it with its own regex.

Two BOXED axes — never conflated, because they mean different things:

  * PART  — which chunk of ONE book (the value that VARIES across a book's files, what the sequencer
            orders): `01 of 15`, `1of9`, `Disc 05`, `CD02`, `Part 3`, `Ch01`, `Tape 2`, `13b`.
  * BOOK  — which book in a series (a book-level ordinal): `Bk01`, `Book 7`, `#16`, `Casca 05`.
            The BOOK box is `identity_tokens.parse_series_ref`; this module owns the PART box.

A PART marker is recognized only on an UNMISTAKABLE signal — a marker word (`Disc`/`Part`/`CD`/…), an
`N of M` total, or an `a`/`b` sub-part half — so a bare number (ambiguous: track? series ordinal?) is
left to context. Whitespace is optional throughout: `1of9`, `CD02`, `Disc05` parse like their spaced
forms. Pure: no I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class SeqKind(Enum):
    PART = "part"   # a chunk of one book (file-level; varies across the book's files)
    BOOK = "book"   # a book in a series (book-level ordinal)


@dataclass(frozen=True)
class SeqMarker:
    """A parsed sequence ordinal. `total` is the M of an `N of M` run (else None). `subpart` is a disc/
    part half (`a`/`b`) or a decimal tail, ordering within an equal `index` and never a gap."""
    kind: SeqKind
    index: int
    total: int | None = None
    subpart: str = ""
    end: int | None = None      # a span's last part ('01-06 of 20' -> index 1, end 6); None = a point

    def covers(self) -> set[int]:
        """The part number(s) this marker accounts for: `{index}` for a point, `{index..end}` for a
        span ('01-06 of 20' covers 1..6). Lets completeness treat a multi-part file as its whole run."""
        return set(range(self.index, (self.end if self.end is not None else self.index) + 1))

    def render(self) -> str:
        """The single unmistakable token form the string passes key off (sentinel-wrapped so it can
        never collide with title text): `‹p5/15›`, `‹p1-6/20›`, `‹p13b›`, `‹b16›`."""
        letter = "p" if self.kind is SeqKind.PART else "b"
        body = str(self.index)
        if self.end is not None:
            body += f"-{self.end}"
        if self.subpart:
            body += self.subpart
        if self.total is not None:
            body += f"/{self.total}"
        return f"‹{letter}{body}›"


# A PART marker word (glued or spaced to its number): disc/tape/track/chapter/part/side/section. NOT
# vol/volume/book/bk — those are the BOOK box (a series volume), kept out so the axes stay separate.
_PART_WORD = r"cd|dvd|disc|disk|dvds?|part|pt|tape|side|track|trk|chap(?:ter)?|ch|sect(?:ion)?"
# One PART token: an optional marker word, the index N, an optional `of M` total, an optional a/b half.
# Whitespace/`.`/`-`/`_` between the word and the number is optional, so 'CD02' and 'Disc 05' both parse.
# `(?!\d)` after each 1-3 digit run keeps a 4-digit year from reading as an index.
_PART = re.compile(
    rf"^\s*(?:(?P<word>{_PART_WORD})\s*[.\-_]?\s*)?"
    r"(?P<n>\d{1,3})(?!\d)"
    r"(?:\s*-\s*(?P<end>\d{1,3})(?!\d))?"        # an optional span end ('01-06 of 20', 'Disc 1-3')
    r"(?:\s*of\s*(?P<m>\d{1,3})(?!\d))?"
    r"(?P<sub>[ab])?\s*$",
    re.IGNORECASE,
)


def parse_part(token: str) -> SeqMarker | None:
    """Parse one token as a PART index, or None. Requires an unmistakable signal — a marker word, an
    `of M` total, or an `a`/`b` half — so a bare number (ambiguous) never matches (a bare `NN-NN` range
    is ambiguous too and is left out). Whitespace optional: 'Disc 05', 'CD02', '1of9', '01 of 15',
    '01-06 of 20', '13b' all parse."""
    m = _PART.match(token)
    if m is None:
        return None
    word, total, sub, end = m.group("word"), m.group("m"), m.group("sub"), m.group("end")
    if not (word or total or sub):        # a bare number/range is ambiguous -> leave to context
        return None
    return SeqMarker(
        kind=SeqKind.PART,
        index=int(m.group("n")),
        total=int(total) if total else None,
        subpart=(sub or "").lower(),
        end=int(end) if end else None,
    )


# The same UNMISTAKABLE PART shapes, matched anywhere in a string (not anchored) so a marker glued to
# title text — 'Hot Mahogany 7of7', 'The Skies of Pern Disc 08 of 11' — is still found. Each alt still
# needs a marker word, an `of M`, or an `a`/`b` half; a bare number is never scanned (too ambiguous
# mid-title). Ordered longest-first so 'Disc 05 of 11' matches whole, not 'Disc 05' then '11'.
_PART_SCAN = re.compile(
    rf"(?<![\w])(?:(?P<word>{_PART_WORD})\s*[.\-_]?\s*)"   # marker word + N [-end] [of M] [a/b]
    r"(?P<n>\d{1,3})(?!\d)(?:\s*-\s*(?P<end>\d{1,3})(?!\d))?"
    r"(?:\s*of\s*(?P<m>\d{1,3})(?!\d))?(?P<sub>[ab])?(?![\w])"
    r"|(?<![\w])(?P<n4>\d{1,3})\s*-\s*(?P<end4>\d{1,3})(?!\d)\s*of\s*(?P<m4>\d{1,3})(?!\d)(?![\w])"  # NN-NN of M span
    rf"|(?<![\w])(?P<n2>\d{{1,3}})(?!\d)\s*of\s*(?P<m2>\d{{1,3}})(?!\d)(?![\w])"  # bare N of M
    r"|(?<![\w])(?P<n3>\d{1,3})(?!\d)(?P<sub3>[ab])(?![\w])",                     # bare NNa / NNb half
    re.IGNORECASE,
)


def _marker_from_scan(m: re.Match[str]) -> SeqMarker:
    n = m.group("n") or m.group("n4") or m.group("n2") or m.group("n3")
    total = m.group("m") or m.group("m4") or m.group("m2")
    sub = m.group("sub") or m.group("sub3") or ""
    end = m.group("end") or m.group("end4")
    return SeqMarker(SeqKind.PART, int(n), int(total) if total else None, sub.lower(),
                     int(end) if end else None)


def find_parts(text: str) -> list[tuple[tuple[int, int], SeqMarker]]:
    """Every PART marker in `text`, as `((start, end), SeqMarker)` in order — including markers glued to
    title text. Only the unmistakable shapes (marker word, `N of M`, `NNa/b`) are scanned."""
    return [((m.start(), m.end()), _marker_from_scan(m)) for m in _PART_SCAN.finditer(text)]


def strip_parts(text: str) -> str:
    """`text` with every PART marker removed and whitespace tidied — the identity material under the
    part index, shared by grouping and title/series extraction so they stop re-deriving the strip."""
    return re.sub(r"\s{2,}", " ", _PART_SCAN.sub(" ", text)).strip(" -_.")
