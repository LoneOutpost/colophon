from colophon.adapters.realdebrid import RdTorrentFile
from colophon.services.filetree import (
    build_file_tree,
    default_selection,
    is_single_audiobook,
)


def test_build_groups_by_folder_selected_only():
    files = [
        RdTorrentFile(id=1, path="/Book A/01.mp3", bytes=100, selected=True),
        RdTorrentFile(id=2, path="/Book A/02.mp3", bytes=200, selected=True),
        RdTorrentFile(id=3, path="/Book B/01.mp3", bytes=50, selected=True),
        RdTorrentFile(id=4, path="/Book B/notes.txt", bytes=5, selected=False),
    ]
    tree = build_file_tree(files)
    assert [n.name for n in tree] == ["Book A", "Book B"]
    assert [f.id for f in tree[0].files] == [1, 2]
    assert tree[0].total_bytes == 300 and tree[0].count == 2
    assert [f.id for f in tree[1].files] == [3]  # notes.txt skipped (not selected)
    assert tree[0].files[0].is_audio is True


def test_root_files_group_under_empty_name():
    tree = build_file_tree([RdTorrentFile(id=1, path="/01.mp3", selected=True)])
    assert tree[0].name == ""


def test_single_audiobook_one_audio_folder():
    tree = build_file_tree([
        RdTorrentFile(id=1, path="/Book/01.mp3", selected=True),
        RdTorrentFile(id=2, path="/Book/cover.jpg", selected=True),
    ])
    assert is_single_audiobook(tree) is True


def test_bundle_multiple_audio_folders():
    tree = build_file_tree([
        RdTorrentFile(id=1, path="/Book A/01.mp3", selected=True),
        RdTorrentFile(id=2, path="/Book B/01.mp3", selected=True),
    ])
    assert is_single_audiobook(tree) is False


def test_default_selection_is_empty():
    # No preselection: the user picks what to download.
    tree = build_file_tree([
        RdTorrentFile(id=1, path="/Book/01.mp3", selected=True),
        RdTorrentFile(id=2, path="/Book/cover.jpg", selected=True),
    ])
    assert default_selection(tree) == set()


def _tree():
    return build_file_tree([
        RdTorrentFile(id=1, path="/Book/Chapter 01.mp3", bytes=1, selected=True),
        RdTorrentFile(id=2, path="/Book/Chapter 02.mp3", bytes=1, selected=True),
        RdTorrentFile(id=3, path="/Book/cover.JPG", bytes=1, selected=True),
    ])


def test_matching_file_ids_case_insensitive_substring():
    from colophon.services.filetree import matching_file_ids

    assert matching_file_ids(_tree(), "chapter") == {1, 2}   # case-insensitive on name
    assert matching_file_ids(_tree(), "02") == {2}
    assert matching_file_ids(_tree(), "jpg") == {3}          # matches uppercase .JPG


def test_matching_file_ids_empty_query_returns_all():
    from colophon.services.filetree import matching_file_ids

    all_ids = {1, 2, 3}
    assert matching_file_ids(_tree(), "") == all_ids
    assert matching_file_ids(_tree(), "   ") == all_ids       # whitespace-only is "no filter"


def test_matching_file_ids_no_match_is_empty():
    from colophon.services.filetree import matching_file_ids

    assert matching_file_ids(_tree(), "zzz") == set()
