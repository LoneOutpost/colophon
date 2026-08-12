"""Cohort-constancy: the filename tokens present in EVERY file of a cohort — the book-identity-material
that survives while per-file indices vary. Engine-neutral primitive; the grouping engine (shared
tokens -> same bucket) and later the field engine (constant token -> identity candidate) consume it.
Single-purpose: no junk filtering, no field assignment. See
tasks/2026-08-11-ballot-engines-architecture.md."""

from __future__ import annotations

import re

_BRACKET = re.compile(r"\([^()]*\)|\[[^\]]*\]")   # a (…) or […] span, non-nested


def _segment_tokens(segment: str) -> list[str]:
    """A ` - ` segment split into tokens: each (…)/[…] span is its own token (brackets retained) and
    the residual text is split around those spans."""
    spans = _BRACKET.findall(segment)             # ['(Outlander 3)']
    residual = _BRACKET.split(segment)            # ['', ' Voyager']  (spans removed)
    tokens = [s.strip() for s in spans] + [p.strip() for p in residual]
    return [t for t in tokens if t]


def _tokens(name: str) -> list[str]:
    """Tokenize a filename stem: normalize the `.-.` folder-variant to ` - `, split on ` - ` (never
    bare `-`/`.`/`_`), then isolate bracketed spans within each segment."""
    out: list[str] = []
    for seg in name.replace(".-.", " - ").split(" - "):
        out.extend(_segment_tokens(seg))
    return out


def _norm(token: str) -> str:
    """Comparison key: casefold + collapse whitespace, so 'Author' and 'author' corroborate."""
    return " ".join(token.casefold().split())


def cohort_constant_tokens(names: list[str]) -> set[str]:
    """Tokens present in EVERY name of the cohort, in display form.

    `names` are filename STEMS (no extension). Cohorts smaller than 2 return an empty set: constancy
    is a cross-file signal, and a lone file has nothing to corroborate. Tokens compare
    case-insensitively; the returned set carries each token's first-seen display casing. Single-purpose:
    no junk filtering and no field assignment."""
    if len(names) < 2:
        return set()
    per_file: list[set[str]] = []
    display: dict[str, str] = {}
    for name in names:
        seen: dict[str, str] = {}
        for tok in _tokens(name):
            seen.setdefault(_norm(tok), tok)
        per_file.append(set(seen))
        for k, v in seen.items():
            display.setdefault(k, v)
    common = set.intersection(*per_file) if per_file else set()
    return {display[k] for k in common}


def cohort_varies_only_by_number(names: list[str]) -> bool:
    """True when, after removing the cohort-constant tokens, every file's remaining material is
    identical once digit-runs are stripped — the files differ ONLY by number (one book's numbered
    parts). `< 2` names -> False. Built on the same tokenizer as `cohort_constant_tokens`."""
    if len(names) < 2:
        return False
    const = {_norm(t) for t in cohort_constant_tokens(names)}
    residues: set[str] = set()
    for name in names:
        rest = [t for t in _tokens(name) if _norm(t) not in const]
        residues.add(_norm(re.sub(r"\d+", " ", " ".join(rest))))
    return len(residues) == 1
