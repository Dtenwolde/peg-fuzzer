"""RunResult and CompareResult dataclasses."""

from dataclasses import dataclass
from enum import Enum, auto


class Outcome(Enum):
    OK = auto()
    ERROR = auto()
    CRASH = auto()


class Parser(Enum):
    PEG = "peg"
    POSTGRES = "postgres"


@dataclass
class RunResult:
    sql: str
    parser: Parser
    outcome: Outcome
    error_msg: str = ""
    duration_ms: float = 0.0


@dataclass
class CompareResult:
    sql: str
    peg: RunResult
    postgres: RunResult

    @property
    def diverged(self) -> bool:
        """True when PEG and Postgres parsers disagree on OK vs ERROR/CRASH."""
        return self.peg.outcome != self.postgres.outcome

    @property
    def any_crash(self) -> bool:
        return self.peg.outcome == Outcome.CRASH or self.postgres.outcome == Outcome.CRASH
