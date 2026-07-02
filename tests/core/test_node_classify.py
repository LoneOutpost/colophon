from pathlib import Path

from colophon.core.graph import DirectoryNode, Graph
from colophon.core.node_classify import Evidence, resolve


def test_hard_evidence_settles_manual_over_matched():
    ev = [
        Evidence("author", 5.0, "matched book author 'Y'", hard=True, value="Y"),
        Evidence("series", 1.0, "one series", hard=False),
        Evidence("author", 9.0, "you classified this", hard=True, value="X"),
    ]
    got = resolve(ev, fallback_value="Folder", manual_kinds={"author"}, matched_kinds={"author"})
    assert got.kind == "author"
    assert got.confidence == 1.0
    assert got.source == "manual"
    assert got.settled is True


def test_soft_argmax_with_margin_confidence():
    ev = [
        Evidence("container", 8.0, "many child folders"),
        Evidence("author", 2.0, "a tag author matches the name"),
    ]
    got = resolve(ev, fallback_value="Folder")
    assert got.kind == "container"
    assert got.source == ""
    assert got.settled is False
    assert 0.7 < got.confidence < 0.85          # 8 / 10
    assert got.value is None                    # container carries no name


def test_soft_author_takes_value_from_evidence_else_folder():
    got = resolve([Evidence("author", 3.0, "artist consensus", value="Isaac Asimov")],
                  fallback_value="Misc SF")
    assert got.kind == "author" and got.value == "Isaac Asimov"
    got2 = resolve([Evidence("author", 3.0, "spans 4 series")], fallback_value="Sarah Graves")
    assert got2.kind == "author" and got2.value == "Sarah Graves"  # no value in evidence -> folder


def test_no_evidence_is_container():
    got = resolve([], fallback_value="X")
    assert got.kind == "container" and got.confidence == 0.0 and got.settled is False


def _dir(graph, path, **kw):
    d = DirectoryNode(path=Path(path), **kw)
    graph.directories[d.id] = d
    return d


def test_container_axioms():
    from colophon.core.node_classify import _Ctx, ax_bucket_word, ax_container_shape

    g = Graph()
    root = Path("/lib")
    bucket = _dir(g, "/lib", child_dirs=["a", "b", "c"])
    ctx = _Ctx(graph=g, root=root, books_by_folder={}, modal_author_depth=None,
               book_like_children={bucket.id: 3})
    kinds = {e.kind for e in ax_container_shape(bucket, ctx)}
    assert kinds == {"container"}

    small = _dir(g, "/lib/x", child_dirs=["a"])
    ctx2 = _Ctx(graph=g, root=root, books_by_folder={}, modal_author_depth=None,
                book_like_children={small.id: 2, bucket.id: 40})
    w_small = sum(e.weight for e in ax_container_shape(small, ctx2))
    w_big = sum(e.weight for e in ax_container_shape(bucket, ctx2))
    assert w_big > w_small

    assert any(e.kind == "container" for e in ax_bucket_word(_dir(g, "/lib/downloads"), ctx))
    assert any(e.kind == "container" for e in ax_bucket_word(_dir(g, "/lib/01"), ctx))
    assert ax_bucket_word(_dir(g, "/lib/Sidney Sheldon"), ctx) == []


def _book(folder, *, authors=(), prov=None, series=None, seq=None):
    from colophon.core.models import BookUnit, SeriesRef
    b = BookUnit.new(source_folder=Path(folder))
    if authors:
        b.authors = list(authors)
        if prov:
            b.provenance["authors"] = prov
    if series:
        b.series = [SeriesRef(name=series, sequence=seq)]
    return b


def test_author_structural_axioms():
    from colophon.core.node_classify import _Ctx, ax_author_structure

    g = Graph()
    root = Path("/lib")
    node = _dir(g, "/lib/star trek")
    books = [_book("/lib/star trek", series=s) for s in ("TOS", "TNG", "DS9")] + [_book("/lib/star trek")]
    ctx = _Ctx(graph=g, root=root, books_by_folder={}, modal_author_depth=2,
               book_like_children={}, direct_books={node.path: books})
    assert any(e.kind == "author" for e in ax_author_structure(node, ctx))   # spans series -> author
    node2 = _dir(g, "/lib/Sidney Sheldon")
    ctx.direct_books[node2.path] = [_book("/lib/Sidney Sheldon") for _ in range(4)]
    assert any(e.kind == "author" for e in ax_author_structure(node2, ctx))  # loose, no series -> author


def test_author_name_and_consensus_axioms():
    from colophon.core.node_classify import _Ctx, ax_artist_consensus, ax_tag_author_match

    g = Graph()
    root = Path("/lib")
    node = _dir(g, "/lib/Brandon Sanderson")
    books = [_book("/lib/Brandon Sanderson", authors=["Brandon Sanderson"], prov="tag") for _ in range(3)]
    ctx = _Ctx(graph=g, root=root, books_by_folder={node.path: books},
               modal_author_depth=None, book_like_children={})
    assert any(e.kind == "author" for e in ax_tag_author_match(node, ctx))
    misc = _dir(g, "/lib/Misc SF")
    ctx.books_by_folder[misc.path] = [_book("/lib/Misc SF", authors=["Isaac Asimov"], prov="tag") for _ in range(3)]
    cons = ax_artist_consensus(misc, ctx)
    assert cons and cons[0].kind == "author" and cons[0].value == "Isaac Asimov"
    mixed = _dir(g, "/lib/Mixed")
    ctx.books_by_folder[mixed.path] = [_book("/lib/Mixed", authors=["A"], prov="tag"),
                                       _book("/lib/Mixed", authors=["B"], prov="tag")]
    assert ax_artist_consensus(mixed, ctx) == []
    # a lone tagged book still names its author (weakly); disagreeing tags produce no consensus
    lone = _dir(g, "/lib/Lone")
    ctx.books_by_folder[lone.path] = [_book("/lib/Lone", authors=["Solo"], prov="tag")]
    lc = ax_artist_consensus(lone, ctx)
    assert lc and lc[0].value == "Solo"


def test_series_and_hard_axioms():
    from colophon.core.models import NodeOverride
    from colophon.core.node_classify import (
        _Ctx,
        ax_manual_override,
        ax_matched_identity,
        ax_series_ramp,
    )

    g = Graph()
    root = Path("/lib")
    mist = _dir(g, "/lib/Mistborn")
    books = [_book("/lib/Mistborn", series="Mistborn", seq=float(i)) for i in (1, 2, 3)]
    ctx = _Ctx(graph=g, root=root, books_by_folder={mist.path: books},
               modal_author_depth=None, book_like_children={})
    sev = ax_series_ramp(mist, ctx)
    assert sev and sev[0].kind == "series"

    hb = _dir(g, "/lib/Robert Jordan")
    ctx.books_by_folder[hb.path] = [_book("/lib/Robert Jordan", authors=["Robert Jordan"], prov="audnexus")]
    mev = ax_matched_identity(hb, ctx)
    assert mev and mev[0].hard is True and mev[0].kind == "author" and mev[0].value == "Robert Jordan"

    node = _dir(g, "/lib/Anything")
    ctx_ov = _Ctx(graph=g, root=root, books_by_folder={}, modal_author_depth=None,
                  book_like_children={}, overrides={"/lib/Anything": NodeOverride(kind="series", value="The Expanse")})
    oev = ax_manual_override(node, ctx_ov)
    assert oev and oev[0].hard is True and oev[0].kind == "series" and oev[0].value == "The Expanse"


def _graph_with(paths_books, root):
    from colophon.core.graph import BookNode
    g = Graph()
    rootnode = DirectoryNode(path=root)
    g.directories[rootnode.id] = rootnode
    for folder, books in paths_books.items():
        d = DirectoryNode(path=Path(folder))
        g.directories[d.id] = d
        rootnode.child_dirs.append(d.id)
        for i, b in enumerate(books):
            bid = f"{d.id}:{i}"
            g.books[bid] = BookNode(id=bid, book=b, owns=[], dir_id=d.id)
            d.books.append(bid)
    return g


def test_classify_nodes_worked_cases(tmp_path):
    from colophon.core.graph_classify import classify_graph
    from colophon.core.node_classify import classify_nodes

    root = tmp_path
    st = [_book(str(root / "star trek"), series=s) for s in ("TOS", "TNG", "DS9", "VOY")]
    mist = [_book(str(root / "Mistborn"), series="Mistborn", seq=float(i)) for i in (1, 2, 3)]
    ss = [_book(str(root / "Sidney Sheldon")) for _ in range(4)]
    poison = [_book(str(root / "Sylvia Plath"), authors=[root.name], prov="datafile")]
    g = _graph_with({
        str(root / "star trek"): st, str(root / "Mistborn"): mist,
        str(root / "Sidney Sheldon"): ss, str(root / "Sylvia Plath"): poison,
    }, root)
    books = [bn.book for bn in g.books.values()]

    classify_graph(g, root=root)
    classify_nodes(g, books, root=root, overrides={})

    def kind(name):
        return g.directories[DirectoryNode.id_for(root / name)].kind

    assert kind("star trek") == "author"
    assert kind("Mistborn") == "series"
    assert kind("Sidney Sheldon") == "author"
    assert g.directories[DirectoryNode.id_for(root)].kind == "container"   # no cascade
    assert all(b.authors == ["star trek"] for b in st)                     # Down-fill
    assert poison[0].authors == [root.name]                                # own author kept


def test_known_franchise_axiom_and_resolution():
    from colophon.core.node_classify import Evidence, _Ctx, ax_known_franchise, resolve

    g = Graph()
    root = Path("/lib")
    st = _dir(g, "/lib/Star Trek")
    ctx = _Ctx(graph=g, root=root, books_by_folder={}, modal_author_depth=None,
               book_like_children={}, known_franchises={"star trek": "Star Trek"})
    ev = ax_known_franchise(st, ctx)
    assert ev and ev[0].kind == "franchise" and ev[0].value == "Star Trek"

    other = _dir(g, "/lib/Isaac Asimov")
    assert ax_known_franchise(other, ctx) == []

    # franchise (4.0) beats a lone grouping-author vote (2.0)
    got = resolve([Evidence("author", 2.0, "grouping"), *ev], fallback_value="Star Trek")
    assert got.kind == "franchise" and got.value == "Star Trek"
