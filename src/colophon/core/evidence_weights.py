"""Centralized evidence weights — the single tuning surface for the weighted-evidence resolvers.
Node-kind classification and field-value resolution both read weights here. In-code constants;
edit + restart to tune. Scale: soft 0.5-5, W_MATCH=10, W_MANUAL=100."""

# --- Evidence weight ladder ------------------------------------------------------------------
# Soft votes SUM; the highest-weighted kind wins. Hard votes (manual/match) settle outright.
# Every axiom draws its weight from here so the precedence story lives in ONE ordered place — read
# top-to-bottom to see what beats what. Relative order matters more than the absolute numbers; when
# adding an axiom, slot its constant into the ladder rather than inventing a bare literal. Distinct
# votes keep distinct names even when values coincide today, so any one can be tuned independently.
W_MANUAL = 100.0            # hard: the user classified this folder
W_MATCH = 10.0             # hard: a matched book's author == the folder name
W_TITLE_LEAF = 5.0         # a single-book leaf folder is that book's title
W_FRANCHISE = 4.0          # folder name == a declared franchise
W_LEAF_SERIES = 4.0        # a lone book whose folder resembles its series
W_SERIES_RAMP = 3.0        # all books one series with a sequence ramp + matching folder name
W_CONSENSUS_MAX = 3.0      # tagged-author consensus, capped (grows 0.5 + 0.5*n up to this)
W_MEMOIR_AUTHOR = 3.0      # a memoir/autobiography titled after its author
W_AUTHOR_STRUCTURE_MAX = 3.0   # loose-books-span-series author vote, CAPPED (else unbounded in n)
W_ROOT_PRIOR = 2.5         # the scan root is usually a library bucket, not one author
W_LEAF_AUTHOR = 2.5        # a lone book sitting at the author depth names the author
W_AUTHOR_GROUPING = 2.0    # a folder of title subfolders (an author/series grouping)
W_BUCKET_WORD = 2.0        # a bucket/staging stop-word folder name
W_NUMERIC_NAME = 1.5       # a numeric folder name is not a person
W_TAG_AUTHOR_MATCH = 1.5   # a tagged author == the folder name (reinforces consensus)
W_MIXED_LOOSE = 1.0        # loose audio beside subfolders
W_NUMBERED_BASE = 1.0      # child names carry sequence numbers (series signal)
W_NUMBERED_RAMP = 2.0      # ...and form a distinct-title numbered ramp
W_NUMBERED_TAG = 1.0       # ...and a child independently asserts a series tag
W_MODAL_DEPTH_NUDGE = 0.5  # sits at the library's typical author depth (tree-consistency nudge)
# Container "bucket" vote grows with the folder-of-folders count: unlike the author vote, MORE
# child book-folders genuinely means MORE bucket-like, so this one is intentionally unbounded.
W_BUCKET_BASE = 1.0
W_BUCKET_PER_CHILD = 0.5

# --- Author field-value resolution (this slice) ---
W_A_TAG = 3.0              # embedded artist tag — a prior, not a guarantee
W_A_DATAFILE = 3.0         # datafile sidecar author
W_A_FOLDER = 2.5           # the author-depth folder name (structural)
W_A_FILENAME = 1.5         # filename $Author (positional pattern parse)
W_A_CONSENSUS_BASE = 0.5   # sibling tag/match consensus: BASE + STEP*n, capped
W_A_CONSENSUS_STEP = 0.5
W_A_CONSENSUS_MAX = 3.0

# --- grouping (Book-bucket one-vs-many election) — provisional, tuned against the real library ---
W_G_PRIOR = 6.0                  # baseline "many" prior; one-evidence must exceed it to merge
W_G_ENUMERATION = 10.0           # files differ only by number -> "one" (heavy, reliable)
W_G_INDEX_TITLES = 10.0          # all per-file titles structural -> "one" (heavy, reliable)
W_G_CONSTANCY_PER_TOKEN = 1.0    # per constant filename token -> "one" (light corroboration)
W_G_CONSTANCY_CAP = 3
W_G_UNIFORM_KEY = 1.5            # shared album/asin/isbn -> "one" (light)
W_G_UNIFORM_AUTHOR = 1.0         # uniform author -> "one" (light)
W_G_SERIES = 12.0                # whole-book-sized files -> "many" (heavy safety)
W_G_SERIES_MIN_SECONDS = 10800   # 3.0h median/file: at/above this the files look like individual books
