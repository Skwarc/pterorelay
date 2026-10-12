"""Console log tailing and player tracking on top of polled Wings console logs."""

from __future__ import annotations

from adapters.engine import GameEvent

# Broadcast senders that are not players: the console, RCON and command blocks.
SYSTEM_SENDERS = frozenset({"Server", "Rcon", "@"})


class ConsoleTailer:
    """Returns only console lines not seen in the previous poll.

    The panel returns the last N console lines on every poll; consecutive
    windows overlap. The first poll only establishes the baseline so the
    backlog is never re-posted after an agent restart.
    """

    def __init__(self) -> None:
        self.previous: list[str] | None = None

    def reset(self) -> None:
        self.previous = None

    def restart(self) -> None:
        """The server stopped; every line of its next run is new."""
        self.previous = []

    def feed(self, lines: list[str]) -> list[str]:
        previous, self.previous = self.previous, list(lines)
        if previous is None:
            return []
        limit = min(len(previous), len(lines))
        for overlap in range(limit, 0, -1):
            if previous[-overlap:] == lines[:overlap]:
                return lines[overlap:]
        return list(lines)


class PlayerTracker:
    """Online players derived from join/leave console events."""

    def __init__(self) -> None:
        self.players: dict[str, None] = {}
        self.known = False

    def reset(self) -> None:
        self.players.clear()
        self.known = False

    def apply(self, event: GameEvent) -> None:
        if event.kind == "server_ready":
            self.reset()
            self.known = True
        elif event.kind == "server_stop":
            self.reset()
        elif event.kind == "join" and event.player:
            self.players[event.player] = None
        elif event.kind == "leave" and event.player:
            self.players.pop(event.player, None)

    @property
    def names(self) -> list[str]:
        return list(self.players)

    def plausible(self, event: GameEvent) -> bool:
        """Whether an event really comes from the game.

        A `/say` from a player prints `[Name] message`, and so do mods and plugins logging
        `[ModTag] something`. A broadcast with a sender name only counts when the sender is
        the console (`Server`, `Rcon`, `@` for command blocks) or a player who is online.
        """
        if event.kind != "broadcast" or not event.player:
            return True
        return event.player in SYSTEM_SENDERS or event.player in self.players
