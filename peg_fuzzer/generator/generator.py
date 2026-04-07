"""Random SQL generator that walks the PEG grammar tree.

Design:
- Build the flat token list for each Rule into a Node tree once (cached).
- At generation time, walk the tree with depth + active-set tracking.
  depth is incremented on every rule-reference crossing.
  active tracks which rules are currently on the call stack; if we are about
  to recurse into a rule already in active AND depth >= max_depth, we return []
  immediately to break the cycle.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Union

from peg_fuzzer.grammar.model import Grammar, PEGToken, PEGTokenType, Rule
from peg_fuzzer.grammar.overrides import OVERRIDES
from peg_fuzzer.generator.terminals import generate_terminal

DEFAULT_MAX_DEPTH = 16

# -------------------------------------------------------------------
# AST nodes (built once per rule body, cached)
# -------------------------------------------------------------------

@dataclass
class LiteralNode:
    text: str


@dataclass
class RefNode:
    ref: str


@dataclass
class SeqNode:
    children: list["Node"] = field(default_factory=list)


@dataclass
class ChoiceNode:
    options: list[SeqNode] = field(default_factory=list)


@dataclass
class OptNode:
    child: "Node"


@dataclass
class RepeatNode:
    child: "Node"
    min_count: int  # 0 for *, 1 for +


@dataclass
class FuncCallNode:
    func_name: str
    arg: "Node"


Node = Union[LiteralNode, RefNode, SeqNode, ChoiceNode, OptNode, RepeatNode, FuncCallNode]


# -------------------------------------------------------------------
# Token list -> Node tree  (parse_choice / parse_item)
# -------------------------------------------------------------------

def _build_tree(tokens: list[PEGToken]) -> Node:
    pos = [0]  # mutable index

    def peek() -> PEGToken | None:
        return tokens[pos[0]] if pos[0] < len(tokens) else None

    def take() -> PEGToken:
        t = tokens[pos[0]]
        pos[0] += 1
        return t

    def parse_choice() -> Node:
        arms: list[SeqNode] = []
        arm = SeqNode()
        while pos[0] < len(tokens):
            t = peek()
            if t is None:
                break
            if t.type == PEGTokenType.OPERATOR and t.text == ")":
                break
            if t.type == PEGTokenType.OPERATOR and t.text == "/":
                take()
                arms.append(arm)
                arm = SeqNode()
                continue
            item = parse_item()
            if item is not None:
                arm.children.append(item)
        arms.append(arm)
        if len(arms) == 1:
            return arms[0]
        return ChoiceNode(options=arms)

    def parse_item() -> Node | None:
        t = peek()
        if t is None:
            return None

        if t.type == PEGTokenType.LITERAL:
            take()
            node: Node = LiteralNode(text=t.text)

        elif t.type == PEGTokenType.REFERENCE:
            take()
            node = RefNode(ref=t.text)

        elif t.type == PEGTokenType.FUNCTION_CALL:
            take()
            func_name = t.text
            arg = parse_choice()
            close = peek()
            if close and close.type == PEGTokenType.OPERATOR and close.text == ")":
                take()
            node = FuncCallNode(func_name=func_name, arg=arg)

        elif t.type == PEGTokenType.OPERATOR:
            op = t.text
            if op == "(":
                take()
                inner = parse_choice()
                close = peek()
                if close and close.type == PEGTokenType.OPERATOR and close.text == ")":
                    take()
                node = inner
            elif op == "!":
                take()  # ignore NOT (same as C++ FIXME comment)
                return None
            else:
                return None  # ), / handled by caller

        elif t.type == PEGTokenType.REGEX:
            raise NotImplementedError(
                f"REGEX tokens are not supported in the generator: {t.text!r}. "
                "Add a rule override for the containing rule."
            )
        else:
            take()
            return None

        # Postfix: ? * +
        nxt = peek()
        if nxt and nxt.type == PEGTokenType.OPERATOR:
            if nxt.text == "?":
                take()
                node = OptNode(child=node)
            elif nxt.text == "*":
                take()
                node = RepeatNode(child=node, min_count=0)
            elif nxt.text == "+":
                take()
                node = RepeatNode(child=node, min_count=1)

        return node

    return parse_choice()


# -------------------------------------------------------------------
# Rule node cache
# -------------------------------------------------------------------

class _RuleCache:
    def __init__(self) -> None:
        self._cache: dict[str, Node] = {}

    def get(self, rule: Rule) -> Node:
        if rule.name not in self._cache:
            self._cache[rule.name] = _build_tree(rule.tokens)
        return self._cache[rule.name]


# -------------------------------------------------------------------
# Generator
# -------------------------------------------------------------------

class Generator:
    def __init__(
        self,
        grammar: Grammar,
        rng: random.Random,
        max_depth: int = DEFAULT_MAX_DEPTH,
        pools: dict | None = None,
    ):
        self.grammar = grammar
        self.rng = rng
        self.max_depth = max_depth
        self.pools = pools or {}
        self._cache = _RuleCache()
        self.rule_hits: Counter[str] = Counter()

    def coverage_stats(self) -> dict:
        """Return a dict with covered/total rule counts and the uncovered rule names."""
        all_rules = set(self.grammar.rules)
        covered = {r for r in all_rules if self.rule_hits.get(r, 0) > 0}
        uncovered = all_rules - covered
        return {
            "total": len(all_rules),
            "covered": len(covered),
            "uncovered": sorted(uncovered),
        }

    def generate(self, start_rule: str = "Statement") -> str:
        parts = self._expand_rule(start_rule, depth=0, active=frozenset(), param_nodes={})
        return " ".join(p for p in parts if p)

    # ------------------------------------------------------------------
    # Expansion
    # ------------------------------------------------------------------

    def _expand_rule(
        self,
        rule_name: str,
        depth: int,
        active: frozenset[str],
        param_nodes: dict[str, Node],
    ) -> list[str]:
        # Terminal overrides (Identifier, NumberLiteral, PlainIdentifier, etc.)
        if rule_name in OVERRIDES:
            return [generate_terminal(OVERRIDES[rule_name], self.rng, self.pools)]

        rule = self.grammar.rules.get(rule_name)
        if rule is None:
            raise KeyError(f"Unknown rule: {rule_name!r}")

        # Break cycles: if this rule is already on the call stack and we're deep, bail
        if rule_name in active and depth >= self.max_depth:
            return []

        self.rule_hits[rule_name] += 1
        new_active = active | {rule_name}
        node = self._cache.get(rule)
        return self._expand_node(node, depth, rule.parameters, new_active, param_nodes)

    def _expand_node(
        self,
        node: Node,
        depth: int,
        rule_params: dict[str, int],
        active: frozenset[str],
        param_nodes: dict[str, Node],
    ) -> list[str]:
        if isinstance(node, LiteralNode):
            return [node.text]

        if isinstance(node, RefNode):
            ref = node.ref
            if ref in param_nodes:
                # Expand an inlined parameter node (e.g. D inside List(D))
                return self._expand_node(param_nodes[ref], depth, {}, active, {})
            return self._expand_rule(ref, depth + 1, active, {})

        if isinstance(node, SeqNode):
            result: list[str] = []
            for child in node.children:
                result.extend(self._expand_node(child, depth, rule_params, active, param_nodes))
            return result

        if isinstance(node, ChoiceNode):
            options = node.options
            if not options:
                return []
            if depth >= self.max_depth:
                # Pick the first alternative (ordered choice -- first is the parser's preferred)
                chosen = options[0]
            else:
                chosen = self.rng.choice(options)
            return self._expand_node(chosen, depth, rule_params, active, param_nodes)

        if isinstance(node, OptNode):
            if depth >= self.max_depth:
                return []
            p = max(0.0, 0.65 - 0.04 * depth)
            if self.rng.random() < p:
                return self._expand_node(node.child, depth + 1, rule_params, active, param_nodes)
            return []

        if isinstance(node, RepeatNode):
            min_count = node.min_count
            if depth >= self.max_depth:
                count = min_count
            else:
                count = min_count
                while count < 5 and self.rng.random() < 0.35:
                    count += 1
            result = []
            for _ in range(count):
                result.extend(
                    self._expand_node(node.child, depth + 1, rule_params, active, param_nodes)
                )
            return result

        if isinstance(node, FuncCallNode):
            func_rule = self.grammar.rules.get(node.func_name)
            if func_rule is None:
                raise KeyError(f"Unknown function rule: {node.func_name!r}")
            if not func_rule.parameters:
                raise ValueError(f"Rule {node.func_name!r} is not parameterised")
            param_name = next(iter(func_rule.parameters))
            new_param_nodes: dict[str, Node] = {param_name: node.arg}
            func_node = self._cache.get(func_rule)
            return self._expand_node(func_node, depth, func_rule.parameters, active, new_param_nodes)

        raise ValueError(f"Unknown node type: {type(node)}")
