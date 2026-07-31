from __future__ import annotations


class PublicationReplayMixin:
    """Carries replay state internally without adding it to public JSON."""

    def _mark_publication_replayed(self, replayed: bool) -> None:
        self._publication_replay_local.replayed = bool(replayed)

    def consume_publication_replay(self) -> bool:
        replayed = bool(getattr(self._publication_replay_local, "replayed", False))
        self._publication_replay_local.replayed = False
        return replayed
