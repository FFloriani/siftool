"""Core domain types for the siftool engine.

Format handlers describe a file as a `Container` of `Block`s, each carrying
zero or more `Finding`s. The planner (`siftool.planner`) then decides, per
block, whether to KEEP / DROP / ZERO / REWRITE it according to the active
`Mode`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


class Category(str, Enum):
    """Kind of information a Finding represents, used to whitelist by Mode."""

    LOCATION = "location"
    IDENTITY = "identity"
    AI_PROVENANCE = "ai_provenance"
    TIME = "time"
    SOFTWARE = "software"
    TEXT = "text"
    EXTRAS = "extras"
    DEVICE = "device"
    CAMERA_SETTINGS = "camera_settings"
    APPEARANCE = "appearance"
    UNKNOWN = "unknown"


class Kind(Enum):
    """Role a Block plays in its container, driving the planner's rules."""

    ESSENTIAL = auto()
    METADATA = auto()
    UNKNOWN = auto()


class Mode(str, Enum):
    """User-facing cleaning mode; selects the allowed Category whitelist."""

    PRIVACY = "privacy"
    PHOTOGRAPHER = "photographer"
    MAXIMUM = "maximum"


class Status(str, Enum):
    """Overall outcome of a post-clean verification."""

    VERIFIED = "verified"
    WARNINGS = "warnings"
    FAILED = "failed"


class Action(Enum):
    """Disposition the planner assigns to a single Block."""

    KEEP = auto()
    DROP = auto()
    ZERO = auto()
    REWRITE = auto()


@dataclass(frozen=True)
class Span:
    """Half-open byte range `[start, end)` within a Container's source."""

    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class Finding:
    """A single piece of metadata detected inside a Block."""

    category: Category
    label: str
    detail: str | None = None
    where: str = ""
    size: int = 0

    def __post_init__(self) -> None:
        if self.detail is not None:
            object.__setattr__(self, "detail", self.detail[:80])


@dataclass
class Block:
    """A structural unit of a file (segment/chunk/box/atom) and its findings."""

    kind: Kind
    span: Span
    label: str
    tag: str
    findings: list[Finding] = field(default_factory=list)
    removable: bool = True
    rewritable: bool = False
    corrupt: bool = False
    raw: bytes = b""


TRAILING_TAG = "TRAILING"


@dataclass
class Container:
    """A parsed file: its format name, ordered blocks and original bytes."""

    format: str
    blocks: list[Block]
    source: bytes
    positional: bool = False

    @property
    def trailing(self) -> Span | None:
        for block in self.blocks:
            if block.tag == TRAILING_TAG:
                return block.span
        return None


@dataclass
class Plan:
    """The planner's decision: `actions[i]` corresponds to `container.blocks[i]`."""

    actions: list[Action]
    replacements: dict[int, bytes] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class Report:
    """Read-only summary of what `inspect` found in a file."""

    path: str
    format: str
    findings: list[Finding]
    essential_digest: str
    notes: list[str]


@dataclass
class Verdict:
    """Outcome of verifying a (cleaned) file against its essential content."""

    status: Status
    violations: list[Finding]
    notes: list[str]
    essential_match: bool | None = None


@dataclass
class CleanResult:
    """Outcome of cleaning one file: what was removed/kept and its Verdict."""

    src: str
    dst: str
    mode: Mode
    removed: list[Finding]
    kept: list[Finding]
    verdict: Verdict
    notes: list[str] = field(default_factory=list)
