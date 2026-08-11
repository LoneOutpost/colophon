from colophon.core import evidence_weights as W


def test_hard_and_node_kind_weights_present():
    assert W.W_MANUAL == 100.0
    assert W.W_MATCH == 10.0
    assert W.W_TITLE_LEAF == 5.0
    assert W.W_LEAF_AUTHOR == 2.5


def test_author_field_weights_present_and_ordered():
    assert W.W_A_TAG == 3.0 and W.W_A_DATAFILE == 3.0
    assert W.W_A_FOLDER == 2.5
    assert W.W_A_FILENAME == 1.5
    assert W.W_A_TAG > W.W_A_FOLDER
    assert W.W_A_FOLDER + W.W_A_FILENAME > W.W_A_TAG
    assert W.W_A_CONSENSUS_MAX == 3.0


def test_node_classify_reexports_moved_constants():
    from colophon.core import node_classify as nc
    assert nc.W_MANUAL == W.W_MANUAL
    assert nc.W_LEAF_AUTHOR == W.W_LEAF_AUTHOR
