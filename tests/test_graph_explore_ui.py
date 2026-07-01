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
