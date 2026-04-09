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


def error_class(msg: str) -> str:
    """Extract the error class prefix from an error message.

    e.g. 'Parser Error: syntax error at ...' -> 'Parser Error'
         'Binder Error: column not found'    -> 'Binder Error'
         ''                                  -> ''
    """
    if not msg:
        return ""
    return msg.splitlines()[0].split(":")[0].strip()


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

    @property
    def any_internal(self) -> bool:
        """True when either parser returns an INTERNAL Error."""
        return (
            error_class(self.peg.error_msg) == "INTERNAL Error"
            or error_class(self.postgres.error_msg) == "INTERNAL Error"
        )
