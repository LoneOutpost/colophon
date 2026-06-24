"""A streamed per-item status list with an end-of-run summary + Retry failed.

Shared by long-running batch dialogs (encode/organize, identify): render rows for a list
of items, stream status updates per item, then show a summary line with optional Retry
failed plus injected extra actions and Close."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from nicegui import ui


@dataclass(frozen=True)
class BatchItem:
    id: str
    label: str


# kind -> caption colour class. kinds: queued | running | ok | warn | skip | fail
_KIND_CLASS = {
    "queued": "colophon-muted",
    "running": "text-primary",
    "ok": "text-positive",
    "warn": "colophon-muted",
    "skip": "colophon-muted",
    "fail": "text-negative",
}


class BatchLog:
    """Render a per-item status list and own the action row. The caller drives the run and
    calls `update` per item, then `finish` with a summary it computes from `counts`."""

    def __init__(self, items: Sequence[BatchItem]) -> None:
        self._kinds: dict[str, str] = {}
        self._captions: dict[str, ui.item_label] = {}
        with ui.scroll_area().classes("w-full").style("max-height: 50vh"):
            with ui.list().props("dense").classes("w-full"):
                for it in items:
                    with ui.item(), ui.item_section():
                        ui.item_label(it.label)
                        cap = ui.item_label("queued").props("caption").classes("colophon-muted")
                        self._captions[it.id] = cap
                        self._kinds[it.id] = "queued"
        self._actions = ui.row().classes("w-full items-center q-gutter-sm q-mt-sm")

    def update(self, item_id: str, status: str, *, kind: str) -> None:
        cap = self._captions.get(item_id)
        if cap is None:
            return
        cap.set_text(status)
        cap.classes(replace=_KIND_CLASS.get(kind, "colophon-muted"))
        self._kinds[item_id] = kind

    def failed_ids(self) -> list[str]:
        return [i for i, k in self._kinds.items() if k == "fail"]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for k in self._kinds.values():
            out[k] = out.get(k, 0) + 1
        return out

    def cancel_action(self, on_cancel: Callable[[], None]) -> None:
        self._actions.clear()
        with self._actions:
            ui.button("Cancel", icon="stop", on_click=on_cancel).props("flat")

    def finish(
        self,
        summary: str,
        *,
        on_close: Callable[[], None],
        on_retry: Callable[[list[str]], object] | None = None,
        retry_label: str = "Retry failed",
        extra: Sequence[tuple[str, str, Callable[[], object]]] = (),
    ) -> None:
        """Replace the action row: summary + each `extra` (label, icon, on_click) button +
        Retry failed (when on_retry is set and there are failed items) + Close. Handlers may
        be async (NiceGUI awaits a returned coroutine)."""
        self._actions.clear()
        with self._actions:
            ui.label(summary).classes("text-body2 q-mr-auto self-center")
            for label, icon, cb in extra:
                ui.button(label, icon=icon, on_click=cb).props("unelevated")
            failed = self.failed_ids()
            if on_retry is not None and failed:
                ui.button(retry_label, icon="replay", on_click=lambda f=failed: on_retry(f))
            ui.button("Close", on_click=on_close).props("flat")
