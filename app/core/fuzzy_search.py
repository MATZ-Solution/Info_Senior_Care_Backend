"""
Typo-tolerant fuzzy text matching, backed by Postgres pg_trgm.

`fuzzy_or_exact(column, value)` returns a SQLAlchemy condition that matches
either an exact/normalized substring match OR a trigram-similarity match --
so a search still finds the right rows even with spelling mistakes (e.g.
'nurshing' -> 'nursing', 'los angeles' -> 'Los Angelas' typo in the data),
while still being at least as good as a plain ILIKE for correctly-spelled
input. Requires the `pg_trgm` extension (see the migration that enables it
and adds GIN trigram indexes on the relevant columns).

Uses word_similarity() rather than plain similarity(): similarity() scores
the two ENTIRE strings against each other and is heavily penalized when
they're very different lengths (e.g. a short typo'd search term like
"nurshing hom" against a long category string like "Nursing Home / Skilled
Nursing Facility" scores far below any reasonable threshold even though it's
a clear match). word_similarity() instead asks "does the first string match
well against SOME part of the second string", which is exactly the "find my
short/typo'd search term somewhere in this field" use case here.
"""
from sqlalchemy import ColumnElement, func, or_


def fuzzy_or_exact(column: ColumnElement, value: str, threshold: float = 0.4) -> ColumnElement:
    return or_(
        column.ilike(f"%{value}%"),
        func.word_similarity(value, column) > threshold,
    )
