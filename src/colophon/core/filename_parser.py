"""Compile a $Token template into a regex and parse filenames into fields.

Uses the shared LazyLibrarian $Token vocabulary (core/tokens). Parseable tokens become
named capture groups keyed by their model field; $Skip discards a run; a run of literal
whitespace matches leniently (\\s+); $$ is a literal $."""

from __future__ import annotations

import re
from re import Pattern

from colophon.core.tokens import token_by_name

_TOKEN = re.compile(r"\$(\w+)")
_DOLLAR_SENTINEL = "\x00DOLLAR\x00"  # protects "$$" through tokenization


def _compile_literal(text: str) -> str:
    """Escape literal text; collapse whitespace runs to \\s+; restore $$ as a literal $."""
    out: list[str] = []
    for chunk in re.split(r"(\s+)", text):
        if not chunk:
            continue
        if chunk.isspace():
            out.append(r"\s+")
        else:
            out.append(re.escape(chunk.replace(_DOLLAR_SENTINEL, "$")))
    return "".join(out)


def compile_template(template: str) -> Pattern[str]:
    """Turn a template like '$Author - $Title' into an anchored regex.

    Parseable tokens become non-greedy named groups; $Skip matches and discards a run;
    literal whitespace is lenient. Raises ValueError on an unknown/non-parseable token,
    a reused field, or a [ ... ] conditional group (those are organize-only)."""
    if "[" in template or "]" in template:
        raise ValueError("[ ... ] conditional groups are only valid in organize patterns, not parse patterns")
    protected = template.replace("$$", _DOLLAR_SENTINEL)
    parts: list[str] = []
    seen: set[str] = set()
    last = 0
    for match in _TOKEN.finditer(protected):
        literal = protected[last : match.start()]
        if literal:
            parts.append(_compile_literal(literal))
        name = match.group(1)
        tok = token_by_name(name)
        if tok is None or not tok.parses:
            raise ValueError(f"Unknown or non-parseable token ${name}")
        if tok.field is None:  # $Skip
            parts.append(r"(?:.+?)")
        else:
            if tok.field in seen:
                raise ValueError(f"Token ${name} used more than once")
            seen.add(tok.field)
            parts.append(rf"(?P<{tok.field}>{tok.pattern})")
        last = match.end()
    trailing = protected[last:]
    if trailing:
        parts.append(_compile_literal(trailing))
    return re.compile("^" + "".join(parts) + "$")


def strip_ext(filename: str) -> str:
    """The filename without its final extension ('a.b.mp3' -> 'a.b')."""
    return filename.rsplit(".", 1)[0] if "." in filename else filename


def parse_filename(pattern: Pattern[str], filename: str) -> dict[str, str] | None:
    """Parse a filename (extension stripped) into field values, or None if no match."""
    match = pattern.match(strip_ext(filename))
    if match is None:
        return None
    return {key: value.strip() for key, value in match.groupdict().items()}
