"""Decides, per Block, whether to KEEP / DROP / ZERO / REWRITE it.

Rules, in order, for each block in `container.blocks`:
- `ESSENTIAL` -> `KEEP`.
- `METADATA`: let `cats` be the categories of its findings and
  `ok = ALLOWED[mode]`.
  - `cats` empty or disjoint from `ok` -> `DROP` (`ZERO` if
    `container.positional`).
  - `cats` subset of `ok` -> `KEEP`.
  - Mixed: if `rewritable`, call `rewrite(block, ok)`; bytes -> `REWRITE`
    (stored in `replacements[i]`); `None` -> `DROP`/`ZERO`. If not
    `rewritable`, `DROP`/`ZERO` plus a note naming what could not be kept.
- `UNKNOWN`: `removable` -> `DROP`/`ZERO`; otherwise `KEEP` plus a note.

In `Mode.MAXIMUM`, if any removed block had an `APPEARANCE` finding,
`MAXIMUM_APPEARANCE_NOTE` is appended once.
"""

from __future__ import annotations

from collections.abc import Callable

from .model import Action, Block, Category, Container, Kind, Mode, Plan
from .modes import ALLOWED

MAXIMUM_APPEARANCE_NOTE = (
    "Maximum mode removed orientation and color information; the image may "
    "display rotated or with different colors."
)


def plan_container(
    container: Container,
    mode: Mode,
    rewrite: Callable[[Block, frozenset[Category]], bytes | None],
) -> Plan:
    ok = ALLOWED[mode]
    actions: list[Action] = []
    replacements: dict[int, bytes] = {}
    notes: list[str] = []
    appearance_removed = False

    for index, block in enumerate(container.blocks):
        cats = frozenset(finding.category for finding in block.findings)

        if block.kind == Kind.ESSENTIAL:
            action = Action.KEEP
        elif block.kind == Kind.METADATA:
            if cats.isdisjoint(ok):
                action = Action.ZERO if container.positional else Action.DROP
            elif cats <= ok:
                action = Action.KEEP
            else:
                new_bytes = rewrite(block, ok) if block.rewritable else None
                if new_bytes is not None:
                    action = Action.REWRITE
                    replacements[index] = new_bytes
                else:
                    action = Action.ZERO if container.positional else Action.DROP
                    if not block.rewritable:
                        kept = ", ".join(sorted(c.value for c in cats & ok))
                        notes.append(
                            f"{block.tag}: could not keep {kept} (block removed)"
                        )
        else:  # Kind.UNKNOWN
            if block.removable:
                action = Action.ZERO if container.positional else Action.DROP
            else:
                action = Action.KEEP
                notes.append(f"Unrecognized structure kept: {block.tag}")

        if mode == Mode.MAXIMUM and action != Action.KEEP and Category.APPEARANCE in cats:
            appearance_removed = True

        actions.append(action)

    if appearance_removed:
        notes.append(MAXIMUM_APPEARANCE_NOTE)

    return Plan(actions=actions, replacements=replacements, notes=notes)
