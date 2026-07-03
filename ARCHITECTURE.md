# Colophon Architecture — Pipeline Component Map

A map of the scan → grouping → classify → identify → entity pipeline: what each
component does, how the layers relate, and — most importantly — where components
**overlap, compete, or are dead**, so we don't build a second, less-capable
version of something that already exists.

> Keep this current. Before adding a component to the pipeline, check the
> **Overlaps** section — the recurring failure mode here is a newer pass quietly
> duplicating an older, capable one (see `infer_from_path` vs `_fill_down`).

---

## 1. Two axes

Work is tracked along **two orthogonal axes**:

- **Per-book lifecycle phases** (`Phase` enum, in order): `SEARCH → CATEGORIZE →
  IDENTIFY → MATCH → TAG → ORGANIZE → ENCODE`. `LOCAL = (SEARCH, CATEGORIZE,
  IDENTIFY)` run synchronously; `DEFERRED = (MATCH, TAG, ORGANIZE, ENCODE)` are
  background jobs. State per phase is `PhaseState` (PENDING/FRESH/STALE/RUNNING/
  FAILED); the legacy `BookState` is *derived* from the phase map by
  `phases.derive_state` (single authority) and cached on `BookUnit.state`.
- **Graph-level scan passes** (`services/ingest.py::plan_scan_graph`): the ordered
  sequence that turns a directory tree into a persisted graph (below).

## 2. The scan passes (order in `plan_scan_graph`)

1. **`build_graph`** (`services/graph_build.py`) — runs `plan_scan` (which runs the
   LOCAL phases per unit), then wraps each `BookUnit` in the node skeleton:
   `DirectoryNode` (+ ancestors), `FileNode` per audio file, `BookNode` (embeds the
   `BookUnit`, `owns` the file ids). A MULTI folder is fanned out into one
   `BookNode` per detected work here (`_leaves_for`, using `content_kind` +
   `detected_works`).
2. **`project`** — reverse-materializes `BookUnit`s from the graph for
   re-association.
3. **adopt + identify** — re-associates projected books to prior DB records
   (preserving durable id/state across content churn) and runs IDENTIFY per leaf.
   This is the long per-book phase (progress reports here since #187).
4. **`classify_graph`** (`core/graph_classify.py`) — coarse directory kinds:
   `grouping / container / title / unknown`.
5. **`classify_nodes`** (`core/node_classify.py`) — the weighted-evidence engine:
   refines nodes to `author / series / franchise / container / title`, then
   `_fill_down` inherits authors into empty/weak books leaf→root.
6. **`propagate_overrides`** (`core/graph_resolve.py`) — stamps confirmed *manual*
   classifications onto books.
7. **`graph_records`** (`core/graph_records.py`) — serializes the graph to
   `NodeRecord`/`EdgeRecord` (structural skeleton + book/entity nodes + edges).
8. **commit** — `GraphStore.replace_subgraph(root, …)` persists it.

View-time (navigator), separate from the scan: `entity_graph_from_records` /
`build_entity_graph` build the semantic `EntityGraph`.

## 3. The graph layers (distinct, complementary — not redundant)

| Layer | Where | Lifetime | Role |
|---|---|---|---|
| **File substrate** | `FileNode` (`core/graph.py`) | in scan | immutable disk facts (path, role, probed audio) |
| **In-memory `Graph`** | `core/graph.py` (`Directory/File/BookNode`) | transient per scan | structural scaffold the classify passes mutate; `BookNode` *embeds* the `BookUnit` |
| **Persisted `GraphStore`** | `adapters/repository/store.py` | durable | `NodeRecord`/`EdgeRecord` in SQLite; replace-by-root |
| **`LibraryGraph`** | `core/library_graph.py` | in-memory, from records at startup | records indexed by id; navigator/admin reads; stale on uncommitted edits |
| **`EntityGraph`** | `core/entity_graph.py` | rebuilt per render | semantic author/series/franchise nodes + book membership; live |

`BookNode.book` embedding the `BookUnit` is intentional (Phase 1); fields migrate
onto the node later.

## 4. Components by subsystem

### 4a. Structure & grouping
- `adapters/scan.py::group_book_units` — filesystem walk → per-folder audio file
  sets (which folders hold books).
- `core/classify.py::group_works` (+ `_work_key`, `_to_work`) — within one folder,
  cluster files into `DetectedWork`s: shared asin/isbn key groups as one book;
  `album` is treated as *ambiguous* (may be a series name) and a multi-file album
  group plus any unkeyed files are handed to `filename_cluster.cluster` to split.
- `core/filename_cluster.py::cluster` — the single filename-structure reasoner
  ("same title differing by number = one book's parts; distinct titles = separate
  books"). Used both directly (fully-untagged folders) and by `group_works`.
- `services/graph_build.py::_leaves_for` — *materializes* the grouping: a MULTI
  folder with >1 work becomes one `BookNode` per work.
- `core/sequence_affix.py::parse_sequence_affix` — the one place that decides whether a name
  carries a sequence-number affix ('02 - Yendi'), its clean title, and confidence (strong =
  spaced/bracketed, weak = unspaced compound). Reused by the numbered-siblings axiom, IDENTIFY
  title cleaning, and `filename_cluster` (one number-stripper, one whitespace guard).

### 4b. Categorize (`core/classify.py`)
- `classify` → `ClassificationResult(content_kind, folder_kind, confidence,
  signals, findings, detected_works)`, written onto the `BookUnit`.
- `content_kind_for` → `ContentKind` SINGLE/MULTI/UNKNOWN.
- `classify_folder_kind` → `FolderKind` AUTHOR/TITLE/UNDETERMINED (see Overlaps —
  largely superseded).
- `_actionable_finding` / `_duplicate_findings` → structural warnings for the UI.

### 4c. Identify / field attribution
- `services/identify.py::run_identify` = `gather → drop_orphaned_datafile_fields →
  seed_series → resolve → attribute → normalize`.
- `core/reconcile.py::reconcile` — per-field precedence ladder: embedded tag >
  datafile sidecar > directory > filename (each field hardcodes its order); stamps
  `Provenance`.
- `core/dirinfer.py::infer_from_path` — directory field extraction, **rigid**: only
  fires when folder depth exactly equals the scheme's level count.
- `core/filename_parser.py` (`compile_template`, `parse_filename`) + `core/tokens.py`
  — the `$Author/$Title/$Series/$SerNum/$PubYear/$Narrator/$Skip` grammar, shared by
  the filename template and the directory scheme.
- `adapters/sidecar.py::read_datafile_sidecar` / `is_container_datafile` — read/vet
  `metadata.json`. `write_datafile_sidecar` is **not called** (see Overlaps).

### 4d. Classification / tiering
- `core/graph_classify.py::classify_graph` — coarse structural kinds (feeds the
  engine as evidence).
- `core/node_classify.py` — the engine: `Evidence` → axioms → `resolve` →
  `Classification`. Axioms (kind / weight): `ax_manual_override` (hard 100),
  `ax_matched_identity` (hard 10), `ax_leaf_title` (title 5), `ax_known_franchise`
  (franchise 7), `ax_series_ramp` (series 3), `ax_artist_consensus` (author ≤3),
  `ax_author_from_grouping` (author 2), `ax_author_structure` (author ~1–2),
  `ax_tag_author_match` (author 1.5), `ax_container_shape` (container; scan-root
  prior 2.5), `ax_bucket_word` (container 2). `_fill_down` does the leaf→root author
  inheritance (with the scheme `author_depth` fallback, #188).
  `ax_numbered_siblings` (series 1–4, additive: trigger + distinct-title ramp + tag corroboration)
- `core/graph_records.py::_ancestor_franchise` — reads `node.kind == "franchise"` to
  emit franchise edges.

### 4e. Entity & naming
- `core/normalize.py::normalize_key` — the **one** canonical comparison key
  (case/spacing/punctuation/underscore/diacritics + guarded PascalCase). Distinct
  from `normalize_name`/`normalize_text` (display formatting).
- `core/graph_resolve.py::_name_key` — thin delegate to `normalize_key` (the shared
  re-export ~8 modules import). Also `_resembles`/`_series_tokens` (fuzzy series
  match), `propagate_overrides`/`apply_confirmed_overrides`/`franchise_for`.
- `core/entity_graph.py` — `build_entity_graph` / `entity_graph_from_records` dedup
  by `normalize_key`; `_canonical_display` (+ `_DISPLAY_AUTHORITY`) picks the display
  spelling by source authority; `resolve_alias` applies user renames.
- `core/navigator.py` — author/series/franchise views over the `EntityGraph`.

### 4f. Stores (`adapters/repository/store.py`) — keyed on different axes
- `NodeOverrideRepo` — **path** → (kind, value): per-folder manual classification.
- `EntityAliasRepo` — **(kind, name_key)** → canonical: entity rename/merge.
- `KnownFranchiseRepo` — **(kind='franchise', name_key)** → display: declared
  franchises.
- `GraphStore` — the persisted property graph.

### 4g. Phase/state (`core/phases.py`)
`mark`, `state_of`, `invalidate_from` (cascade), `derive_state` (the only
phase→`BookState` translator), `resync_state`, `ensure_phases` (legacy seed).

---

## 5. Overlaps, redundancies & competing implementations

The point of this document. Status legend: **⚠ competing** (two implementations of
the same job — consolidation candidate), **☑ complementary** (looks redundant, is
not — documented boundary), **🪦 dead** (kept but unused), **✅ resolved** (a past
overlap already consolidated — the pattern to repeat).

### ⚠ Two directory→author attributors: `infer_from_path` vs `_fill_down`
The clearest overlap, and a cautionary tale: #188 added a *second* mechanism rather
than fixing the first.
- `infer_from_path` (`core/dirinfer.py`, in IDENTIFY): extracts an author from the
  path via the scheme, **rigid** (exact depth match), stamped `directory` in
  `reconcile`.
- `_fill_down` `author_depth` fallback (`core/node_classify.py`, post-classification,
  #188): binds the folder at the scheme's author depth, **depth-flexible**, stamped
  `directory`, only for empty/weak-author books.

They coexist at different pipeline stages (`_fill_down` is a later, softer
fallback), so there's no hard conflict today — but they are two answers to "who is
the author from the folder layout?". The **late-binding scan architecture**
(`docs/superpowers/specs/2026-07-02-scan-architecture-design.md`) unifies them:
identity resolves *after* classification from settled nodes. Consolidate there;
until then, do not add a third path.

### 🪦 `FolderKind` (`classify.py::classify_folder_kind`) is near-dead
`book.folder_kind` (AUTHOR/TITLE/UNDETERMINED) is computed every CATEGORIZE but the
authoritative folder classification is `DirectoryNode.kind` from `classify_nodes`.
The only remaining reader of `folder_kind` is a datafile-vetting check in
`identify.py` (`is_container_datafile`). Treat `folder_kind` as CATEGORIZE-local;
prefer `node.kind`. Candidate for removal once the datafile check is re-expressed.

### 🪦 `write_datafile_sidecar` is intentionally dead
Kept for a future explicit "export to ABS" utility; **no production caller**
(colophon does not write `metadata.json` — ABS's domain). Do not wire it into edits.

### ⚠ `_WEAK` provenance set defined in 3 places
`node_classify._WEAK` and `graph_resolve._WEAK` = `{directory, filename}`;
`triage`'s weak set adds `graphing`. Minor, but a real drift risk — consolidate to
one shared definition (a provenance-tier helper) when convenient.

### ☑ `group_book_units` vs `group_works` — complementary
Different granularities: `group_book_units` is the filesystem walk (which folders
have audio); `group_works` is within-folder file→work clustering. Sequential, not
competing. `_leaves_for` then materializes `group_works`'s decision as `BookNode`s —
the grouping decision is made once (CATEGORIZE) and executed once (build).

### ☑ Graph / GraphStore / LibraryGraph / EntityGraph — complementary layers
Not redundant representations: structural-transient / persisted-records /
materialized-at-startup / semantic-view. See §3.

### ☑ Directory scheme vs filename template — complementary
Both use the `$Token` grammar but read different sources (path components vs file
stem). Their outputs feed `reconcile`, which picks per field by precedence.

### ☑ Module-local `_norm` helpers (`classify.py`, `match.py`) — acceptable
Domain-specific low-level string helpers, **not** entity-naming. Do NOT promote
either to a shared key — that role belongs to `normalize_key` alone.

### ✅ Resolved overlaps (the pattern to repeat)
- `_name_key` **consolidated** into `normalize_key` (#190) — one canonical key, all
  callers delegate.
- `resolve_graph_authors` / `hint_grouping_kinds` **deleted** when the evidence
  engine subsumed them (their behavior + tests moved to `node_classify`).
- Dedup vs display **split**: `normalize_key` ("same entity?") vs `_canonical_display`
  ("which spelling?") — two clearly-separated jobs, not one fuzzy one.
- Filename-structure reasoning **consolidated** into `filename_cluster.cluster`
  (#189): `group_works` used to carry its own weaker numbered-sequence detector
  (`_is_single_sequence`) that disagreed with `cluster` on the same input. It now
  delegates ambiguous album groups and unkeyed files to `cluster`, so there is one
  answer to "same book's parts vs distinct books".
- Number-stripping **consolidated** onto `sequence_affix.parse_sequence_affix`: `filename_cluster`
  no longer drops a leading number blindly (which mangled '30-Day Heart Tune-Up'); it shares the
  strong/weak whitespace guard with IDENTIFY and the classifier.

---

## 6. Where new work most often collides

When touching these areas, check for an existing owner first:
- **Author-from-directory** → `infer_from_path` + `_fill_down` (already two; unify,
  don't add).
- **Name comparison / dedup** → `normalize_key` only.
- **Folder classification** → `node.kind` (authoritative); `folder_kind` is legacy.
- **File→book grouping** → `group_works` (+ `_leaves_for` to materialize).
- **"Which spelling to show"** → `_canonical_display` / `_DISPLAY_AUTHORITY`.
