"""Split a delimited author/narrator string into individual names.

Sources sometimes deliver several people as one delimited string. This is the
single, shared splitter. In auto mode it uses a conservative full-name
heuristic; when a caller knows the provider's exact delimiter(s) it passes
`separators` to remove the ambiguity entirely.
"""

from __future__ import annotations

import re

from colophon.core.normalize import normalize_key

# Unambiguous multi-person separators always split in auto mode: '&', a
# whitespace-bounded 'and', and ';'. (Commas are handled separately.)
_AUTO_SEPARATORS = re.compile(r"\s*&\s*|\s+and\s+|\s*;\s*")

# A byline prefix is not part of anyone's name. Folders and tags routinely carry one ("By Bernard
# Cornwell"), and leaving it attached made the same author compare unequal to its own bare spelling,
# which surfaced as a false metadata conflict. The trailing \s+ keeps 'Byron' and 'Bywater' intact.
_BYLINE_PREFIX = re.compile(r"^\s*(?:written\s+by|author:?|by)\s+", re.IGNORECASE)


def _looks_like_full_name(part: str) -> bool:
    """A 'First Last' name has internal whitespace; 'Frank' or 'Jr.' does not."""
    return " " in part.strip()


def split_people(value: str | None, *, separators: list[str] | None = None) -> list[str]:
    """Split `value` into individual people. `None`/blank -> []. Parts are
    stripped and empties dropped.

    Hinted mode (`separators` given): split on exactly those delimiters; any
    other character (commas included) is treated as name-internal.

    Auto mode (`separators is None`): split on '&'/' and '/';', then split a
    chunk on commas only when it has >=2 comma-parts that all look like full
    names (contain internal whitespace), so 'Last, First' and suffixes are kept.
    """
    if not value or not value.strip():
        return []
    value = _BYLINE_PREFIX.sub("", value)
    if not value.strip():
        return []

    if separators is not None:
        pattern = "|".join(re.escape(sep) for sep in separators)
        parts = re.split(pattern, value) if pattern else [value]
        return [p.strip() for p in parts if p.strip()]

    out: list[str] = []
    for chunk in _AUTO_SEPARATORS.split(value):
        chunk = chunk.strip()
        if not chunk:
            continue
        comma_parts = [p.strip() for p in chunk.split(",") if p.strip()]
        if len(comma_parts) >= 2 and all(_looks_like_full_name(p) for p in comma_parts):
            out.extend(comma_parts)
        else:
            out.append(chunk)
    return out


def names_disagree(folder_name: str, value: str) -> bool:
    """Whether an author folder's own name and the author value elected for it name different
    people outright: their normalized words share nothing at all. Deliberately loose, because a
    folder is spelled however its owner liked ('Tolkien' for 'J.R.R. Tolkien', 'Connelly, Michael'
    for 'Michael Connelly', 'Clarke, Baxter' for 'Clarke and Baxter'); any shared word is agreement.
    Word-disjointness also implies the people-sets differ with neither containing the other, so the
    `_fill_down` people-set comparison needs no separate check here. Blank on either side is not a
    disagreement: there is nothing to compare."""
    folder_words = {w for p in split_people(folder_name) for w in normalize_key(p).split()}
    value_words = {w for p in split_people(value) for w in normalize_key(p).split()}
    return bool(folder_words and value_words) and folder_words.isdisjoint(value_words)


# A single name token: an initial ('A', 'A.', 'AC', 'AJ'), a capitalized word ('Robinson', "O'Brien",
# 'Jean-Luc'), or a lowercase name particle ('van', 'de', 'von', 'la', 'di'). No digits, brackets, '#'.
_NAME_TOKEN = re.compile(r"^(?:[A-Z][A-Za-z'’.\-]*|van|von|de[rnl]?|del|di|la|le|du|da|dos)$")  # noqa: RUF001
_NAME_PARTICLE = {"van", "von", "de", "der", "den", "del", "di", "la", "le", "du", "da", "dos"}


def looks_like_person_name(value: str | None) -> bool:
    """A conservative 'this string is shaped like a person's name (or names)' test — the guard for
    reading the FIRST `.-.` leaf-folder segment as the author. It confirms NAME SHAPE only; it does not
    (and cannot) tell an author from a same-shaped title. Each people-part (split on &/and/;/comma) must
    be 1-4 tokens, every token an initial, a capitalized word, or a lowercase particle, with at least
    one real letter-word — so 'AC Crispin', 'A. Lee Martinez', 'Kim Stanley Robinson', 'AJ Hartley and
    David Hewson' pass; '[Ciaphas Cain 13] …', '1984', 'the collected works' are rejected."""
    if not value or not value.strip():
        return False
    parts = split_people(value)
    if not parts:
        return False
    for part in parts:
        tokens = part.split()
        if not (1 <= len(tokens) <= 4):
            return False
        if not all(_NAME_TOKEN.match(t) for t in tokens):
            return False
        if not any(t.lower() not in _NAME_PARTICLE and any(ch.isalpha() for ch in t) for t in tokens):
            return False
    return True
