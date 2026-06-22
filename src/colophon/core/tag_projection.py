"""Project a BookUnit's confirmed fields into the EmbeddedTags write model.

The single mapping between Colophon's domain model and the on-disk tag
vocabulary. List fields join on '; ' (matching the editor's author/narrator
display); series/sequence come from the first SeriesRef. `album` mirrors the
title, the common audiobook convention. The tag vocabulary intentionally matches
the read side (EmbeddedTags) for round-trip symmetry; subtitle/publisher/language
are not part of the embedded tag set and are carried only in the sidecar.
"""

from __future__ import annotations

from colophon.core.models import BookUnit, EmbeddedTags


def project_tags(book: BookUnit) -> EmbeddedTags:
    first_series = book.series[0] if book.series else None
    return EmbeddedTags(
        title=book.title,
        album=book.title,
        artist="; ".join(book.authors) or None,
        narrator="; ".join(book.narrators) or None,
        series=first_series.name if first_series else None,
        sequence=first_series.sequence if first_series else None,
        year=book.publish_year,
        genre="; ".join(book.genres) or None,
        description=book.description,
        asin=book.asin,
        isbn=book.isbn,
    )
