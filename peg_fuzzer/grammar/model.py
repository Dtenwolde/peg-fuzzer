"""Grammar IR model mirroring peg_parser.hpp data structures."""

from dataclasses import dataclass, field
from enum import Enum, auto


class PEGTokenType(Enum):
    LITERAL = auto()        # 'keyword'
    REFERENCE = auto()      # OtherRule
    OPERATOR = auto()       # / ? * + ( ) !
    FUNCTION_CALL = auto()  # Rule(arg) -- e.g. List(X)
    REGEX = auto()          # [chars] or <pattern>


@dataclass
class PEGToken:
    type: PEGTokenType
    text: str


@dataclass
class Rule:
    name: str
    parameters: dict[str, int] = field(default_factory=dict)
    tokens: list[PEGToken] = field(default_factory=list)


@dataclass
class Grammar:
    rules: dict[str, Rule] = field(default_factory=dict)

    def add_rule(self, rule: Rule) -> None:
        if rule.name in self.rules:
            raise ValueError(f"Duplicate rule name: {rule.name}")
        self.rules[rule.name] = rule
