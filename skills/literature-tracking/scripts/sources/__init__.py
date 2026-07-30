"""Per-source adapters.

Each exposes ``search(...) -> SearchResult``, which carries the papers together
with how much the API said existed — see ``models.SearchResult``. It defines
``__len__`` but not ``__iter__``, so iterate ``.papers``, and test it against
``None`` rather than for truthiness: an empty result is falsy.
"""
