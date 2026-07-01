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
