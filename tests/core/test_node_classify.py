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
