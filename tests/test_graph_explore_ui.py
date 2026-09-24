from colophon.ui.graph_view import _graph_url, _parse_hidden


def test_parse_hidden_keeps_known_kinds_only():
    assert _parse_hidden("book,file,junk") == frozenset({"book", "file"})
    assert _parse_hidden(None) == frozenset()
    assert _parse_hidden("") == frozenset()
    assert _parse_hidden("author") == frozenset({"author"})


def test_graph_url_encodes_focal_and_sorted_hide():
    assert _graph_url("book:1", frozenset()) == "/graph?focal=book%3A1"
    # kinds are joined sorted, comma-separated, and NOT percent-encoded (so parse round-trips)
    assert _graph_url("book:1", frozenset({"file", "book"})) == "/graph?focal=book%3A1&hide=book,file"


def test_round_trip_parse_of_graph_url_hide():
    hidden = frozenset({"file", "folder"})
    url = _graph_url("author:abc", hidden)
    tail = url.split("hide=", 1)[1]
    assert _parse_hidden(tail) == hidden


def test_legend_helper_is_callable():
    from colophon.ui.graph_view import _explorer_legend

    assert callable(_explorer_legend)


def test_parse_depth_clamps_to_1_3():
    from colophon.ui.graph_view import _parse_depth

    assert _parse_depth(None) == 1
    assert _parse_depth("2") == 2
    assert _parse_depth("0") == 1      # clamp low
    assert _parse_depth("9") == 3      # clamp high
    assert _parse_depth("nope") == 1   # non-numeric
    assert _parse_depth(2.0) == 2      # spinner emits floats


def test_graph_url_includes_depth_and_hide():
    from colophon.ui.graph_view import _graph_url

    assert _graph_url("book:1", frozenset(), depth=1) == "/graph?focal=book%3A1"
    assert _graph_url("book:1", frozenset(), depth=2) == "/graph?focal=book%3A1&depth=2"
    assert _graph_url("book:1", frozenset({"file"}), depth=3) == "/graph?focal=book%3A1&depth=3&hide=file"


def test_panel_helper_is_callable():
    from colophon.ui.graph_view import _explorer_panel

    assert callable(_explorer_panel)


def test_node_click_target_navigates_only_on_node():
    from colophon.ui.graph_view import _node_click_target

    none = frozenset()
    # a graph node click -> navigate to that focal
    assert _node_click_target(
        {"componentType": "series", "dataType": "node", "data": {"id": "book:1"}}, none
    ) == "/graph?focal=book%3A1"
    # an edge click (ECharts omits 'value') -> ignored, never crashes
    assert _node_click_target(
        {"componentType": "series", "dataType": "edge", "data": {}}, none
    ) is None
    # a non-series click (e.g. roam/background) -> ignored
    assert _node_click_target({"componentType": "grid"}, none) is None
    # a node with no id -> ignored
    assert _node_click_target(
        {"componentType": "series", "dataType": "node", "data": {}}, none
    ) is None
    # the hidden set is preserved into the target URL
    assert _node_click_target(
        {"componentType": "series", "dataType": "node", "data": {"id": "a"}}, frozenset({"file"})
    ) == "/graph?focal=a&hide=file"


def test_resolve_tree_focus_opens_the_root_holding_a_directory():
    from pathlib import Path

    from colophon.ui.graph_view import resolve_tree_focus

    roots = [Path("/lib/a"), Path("/lib/b")]
    folder = Path("/lib/b/Author/Book")
    dirs = {"dir:1": (folder, "title"), "dir:root": (Path("/lib/a"), "")}
    lookup = dirs.get

    assert resolve_tree_focus("dir:1", roots, lookup) == (Path("/lib/b"), folder)
    assert resolve_tree_focus("dir:root", roots, lookup) == (Path("/lib/a"), Path("/lib/a"))


def test_resolve_tree_focus_ignores_non_directories_and_outsiders():
    from pathlib import Path

    from colophon.ui.graph_view import resolve_tree_focus

    roots = [Path("/lib/a")]
    lookup = {"dir:out": (Path("/elsewhere/x"), "author")}.get

    assert resolve_tree_focus(None, roots, lookup) is None
    assert resolve_tree_focus("book:1", roots, lookup) is None   # not a directory node
    assert resolve_tree_focus("dir:out", roots, lookup) is None  # outside every root


def test_resolve_tree_focus_prefers_the_deepest_nested_root():
    from pathlib import Path

    from colophon.ui.graph_view import resolve_tree_focus

    roots = [Path("/lib"), Path("/lib/audio")]
    folder = Path("/lib/audio/Series/Book 1")
    assert resolve_tree_focus("d", roots, {"d": (folder, "series")}.get) == (Path("/lib/audio"), folder)
