"""Pure helpers to snapshot the workspace view to a JSON-safe dict and restore it,
dropping any stale references (vanished author/series/book) to safe defaults."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DEFAULT_VIEW = {"mode": "library", "cwd": None, "multiselect": False, "group_by": "author"}


@dataclass
class RestoredView:
    scope: dict[str, Any]
    folder_filter_path: Path | None
    view: dict[str, Any]
    filter_text: str
    selected_ids: set[str]
    open_book_id: str | None


def view_to_snapshot(
    *, scope: dict[str, Any], folder_filter: dict[str, Any], view: dict[str, Any],
    filter_text: str, selected_ids: set[str], open_book_id: str | None,
) -> dict[str, Any]:
    cwd = view.get("cwd")
    ff = folder_filter.get("path")
    return {
        "scope": {"kind": scope.get("kind", "all"), "key": scope.get("key")},
        "folder_filter": str(ff) if ff else None,
        "view": {
            "mode": view.get("mode", "library"),
            "cwd": str(cwd) if cwd else None,
            "multiselect": bool(view.get("multiselect")),
            "group_by": view.get("group_by", "author"),
        },
        "filter": filter_text or "",
        "selected_ids": sorted(selected_ids),
        "open_book_id": open_book_id,
    }


def snapshot_to_view(
    snap: dict[str, Any] | None, *,
    known_book_ids: set[str], known_authors: set[str], known_series: set[str],
) -> RestoredView:
    if not snap:
        return RestoredView(
            scope={"kind": "all", "key": None}, folder_filter_path=None,
            view=dict(_DEFAULT_VIEW), filter_text="", selected_ids=set(), open_book_id=None,
        )
    raw_scope = snap.get("scope") or {}
    kind, key = raw_scope.get("kind", "all"), raw_scope.get("key")
    if (kind == "author" and key not in known_authors) or (kind == "series" and key not in known_series):
        kind, key = "all", None

    raw_view = snap.get("view") or {}
    view = {
        "mode": raw_view.get("mode", "library"),
        "cwd": Path(raw_view["cwd"]) if raw_view.get("cwd") else None,
        "multiselect": bool(raw_view.get("multiselect")),
        "group_by": raw_view.get("group_by", "author"),
    }
    ff = snap.get("folder_filter")
    open_id = snap.get("open_book_id")
    return RestoredView(
        scope={"kind": kind, "key": key},
        folder_filter_path=Path(ff) if ff else None,
        view=view,
        filter_text=snap.get("filter") or "",
        selected_ids={i for i in (snap.get("selected_ids") or []) if i in known_book_ids},
        open_book_id=open_id if open_id in known_book_ids else None,
    )
