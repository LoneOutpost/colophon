# Identity tools

Reusable, pure utilities for judging and normalizing book-identity values (title, author, series).
They are **tools in a toolbox** — a process (scan clustering, title corroboration, field repair,
match) composes the ones it needs; none are baked into a single process.

## Junk detectors — `metadata_quality.py`
- `is_placeholder_title(value)` — a rip placeholder ("Track 3", "Unknown Album", "Untitled").
- `is_index_title(value)` — a bare number or track-of-total ("15", "01 of 15").
- `is_junk_title(value)` — the umbrella: empty / placeholder / track-marker / index.
- `is_title_shaped_author(author, title=None)` — an author value that is really a title.

## Tokenize / signature
- `filename_cluster._spaced / _tokens / _text_sig` — display/clustering tokenizer (drops index
  tokens to build a per-part signature).
- `title_corroborate._words / _title_words` — meaning tokenizer (drops stopwords/markers).

## Compare
- `filename_cluster.shares_token` — share a non-numeric word ≥2 chars (keeps stopwords).
- `title_corroborate._shares_word` — share a *meaningful* word (drops stopwords).
- `match.ratio`, `match.title_author_score` — fuzzy similarity.
- `classify._text_key`, `normalize.normalize_key` — comparison keys (free text vs entity names).
- `author_merge._lev` — bounded Levenshtein.

## Affix / number strippers — `sequence_affix.py`
- `parse_sequence_affix`, `strip_series_code_affix`, `strip_series_book_suffix`.

## Normalize
- `normalize.normalize_text / normalize_name / normalize_key / proper_case_if_shouting`.
- `match.clean_match_title`; `field_repair.repair_fields`.

## Names / people — `people.py`
- `split_people`, `split_author`.

## Structure parsers
- `folder_title.parse_folder_title`; `filename_parser.parse_filename / compile_template`;
  `dirinfer.parse_scheme / infer_from_path`.

## Determinators (compose the above)
- `title_corroborate.corroborate_title / book_title_verdict`; `author_merge.suggest_author_merges`;
  `classify._pick_single_title / group_works`.

## Known overlaps (future consolidation, not yet done)
- `shares_token` vs `_shares_word` — same idea, different stopword handling.
- `_text_key` vs `normalize_key` — two comparison keys.
- three tokenizers (`filename_cluster`, `title_corroborate`) — different intent; not merged.
