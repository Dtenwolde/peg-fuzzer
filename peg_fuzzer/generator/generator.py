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


@dataclass
class GenContext:
    """Semantic names accumulated while generating a single statement.

    Reset at the start of every generate() call so each statement gets a
    clean slate.  Rules that *define* a name append to the relevant list;
    rules that *reference* a name pick from it (falling back to a safe
    default when the list is empty).
    """
    window_names: list[str] = field(default_factory=list)
    cte_names: list[str] = field(default_factory=list)

    def reset(self) -> None:
        self.window_names.clear()
        self.cte_names.clear()

from peg_fuzzer.grammar.model import Grammar, PEGToken, PEGTokenType, Rule
from peg_fuzzer.grammar.overrides import OVERRIDES
from peg_fuzzer.generator.catalog import FUZZ_SCHEMA
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
        coverage_hits: dict[str, int] | None = None,
    ):
        self.grammar = grammar
        self.rng = rng
        self.max_depth = max_depth
        self.pools = pools or {}
        self.coverage_hits: dict[str, int] = coverage_hits or {}
        self._cache = _RuleCache()
        self.rule_hits: Counter[str] = Counter()
        self._ctx = GenContext()
        # Build valid (table, col) pairs for schema-aware ColumnReference generation.
        # Only populated when schema pools are loaded (fuzzer mode); empty otherwise.
        _col_kind = OVERRIDES.get("ColumnName")
        if _col_kind is not None and self.pools.get(_col_kind):
            self._table_col_pairs: list[tuple[str, str]] = [
                (table, col)
                for table, cols in FUZZ_SCHEMA.items()
                for col, _ in cols
            ]
        else:
            self._table_col_pairs = []

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
        self._ctx.reset()
        parts = self._expand_rule(start_rule, depth=0, active=frozenset(), param_nodes={})
        return " ".join(p for p in parts if p)

    # ------------------------------------------------------------------
    # Coverage-guided arm selection
    # ------------------------------------------------------------------

    @staticmethod
    def _primary_ref(seq: SeqNode) -> str | None:
        """Return the name of the first RefNode child, ignoring LiteralNodes."""
        for child in seq.children:
            if isinstance(child, RefNode):
                return child.ref
        return None

    def _weighted_choice(self, options: list[SeqNode]) -> SeqNode:
        """Pick a ChoiceNode arm weighted inversely by cumulative hit count.

        Arms whose primary rule has never been hit get weight 1.0; arms with N
        hits get weight 1/(N+1), so cold arms are preferred proportionally.
        If all weights are equal (no coverage data) this degrades to uniform random.
        """
        weights = []
        for arm in options:
            ref = self._primary_ref(arm)
            hits = self.coverage_hits.get(ref, 0) if ref is not None else 0
            weights.append(1.0 / (hits + 1))
        total = sum(weights)
        r = self.rng.random() * total
        cumulative = 0.0
        for arm, w in zip(options, weights):
            cumulative += w
            if r <= cumulative:
                return arm
        return options[-1]

    # ------------------------------------------------------------------
    # Simple-option selection (used at max depth)
    # ------------------------------------------------------------------

    def _pick_simple_option(self, options: list[SeqNode]) -> SeqNode:
        """Return the first option that expands without recursive grammar rules.

        Falls back to options[0] if none qualifies (preserving old behaviour).
        """
        for opt in options:
            if self._is_simple_node(opt, frozenset()):
                return opt
        return options[0]

    def _is_simple_node(self, node: Node, visited: frozenset[str]) -> bool:
        """True if node can be fully expanded using only overrides, literals,
        optionals (skipped), and zero-count repeats -- no recursive grammar rules.

        visited guards against cycles in the grammar.
        """
        if isinstance(node, LiteralNode):
            return True
        if isinstance(node, RefNode):
            if node.ref in OVERRIDES:
                return True
            if node.ref in visited:
                return False  # cycle -- treat as non-simple
            rule = self.grammar.rules.get(node.ref)
            if rule is None:
                return False
            return self._is_simple_node(self._cache.get(rule), visited | {node.ref})
        if isinstance(node, SeqNode):
            return all(self._is_simple_node(c, visited) for c in node.children)
        if isinstance(node, OptNode):
            return True  # can always emit nothing
        if isinstance(node, ChoiceNode):
            return any(self._is_simple_node(opt, visited) for opt in node.options)
        if isinstance(node, RepeatNode):
            return node.min_count == 0  # * can emit zero items
        # FuncCallNode: parameterised rule -- assume not simple
        return False

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
        # TableName: mix in CTE names alongside the fuzz schema tables so that
        # queries referencing CTEs actually refer to something that was defined.
        if rule_name == "TableName" and self._ctx.cte_names:
            pool = self.pools.get(OVERRIDES["TableName"], []) + self._ctx.cte_names
            return [self.rng.choice(pool)]

        # Terminal overrides (Identifier, NumberLiteral, PlainIdentifier, etc.)
        if rule_name in OVERRIDES:
            return [generate_terminal(OVERRIDES[rule_name], self.rng, self.pools)]

        # Context-aware rules: semantic constraints the grammar cannot express.
        if rule_name == "BaseWindowName":
            # Refers to another window definition in the same WINDOW clause.
            # Only emit a name when one has actually been defined; otherwise
            # produce nothing (BaseWindowName is always optional in context).
            if self._ctx.window_names:
                return [self.rng.choice(self._ctx.window_names)]
            return []

        if rule_name == "WindowDefinition":
            # WindowDefinition <- Identifier 'AS' WindowFrameDefinition
            # Expand manually so we can register the window name before the
            # frame definition is generated (enabling BaseWindowName inside
            # multi-window clauses to reference earlier windows).
            return self._expand_window_definition(depth, active)

        if rule_name == "WindowFrame":
            # WindowFrame <- ParensIdentifier / WindowFrameDefinition / Identifier
            # The bare Identifier arm is a named-window reference (e.g. OVER w).
            # Only allow it when window names are already registered; otherwise it
            # creates a dangling reference and a spurious divergence.
            return self._expand_window_frame(depth, active)

        if rule_name == "WithStatement":
            # WithStatement <- ColIdOrString InsertColumnList? UsingKey? 'AS' Materialized? CTEBody
            # Expand ColIdOrString first to capture the CTE name, then expand the rest.
            return self._expand_with_statement(depth, active)

        if rule_name == "ColumnReference":
            # ColumnReference can expand to table-qualified arms that mismatch the schema
            # (e.g. t1.col_c where col_c is only on t). When schema pools are loaded,
            # emit either a bare column name or a coherent (table, col) pair.
            _col_kind = OVERRIDES.get("ColumnName")
            if _col_kind is not None:
                col_pool = self.pools.get(_col_kind, [])
                if col_pool:
                    if self._table_col_pairs and self.rng.random() < 0.4:
                        table, col = self.rng.choice(self._table_col_pairs)
                        return [table, ".", col]
                    return [self.rng.choice(col_pool)]
            # No pool -- fall through to normal grammar expansion.

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

    # ------------------------------------------------------------------
    # Context-aware helpers
    # ------------------------------------------------------------------

    def _expand_with_statement(self, depth: int, active: frozenset[str]) -> list[str]:
        """Expand WithStatement and register the CTE name in context.

        WithStatement <- ColIdOrString InsertColumnList? UsingKey? 'AS' Materialized? CTEBody

        We expand ColIdOrString first and register its result so that subsequent
        table-name references inside the same statement can refer to this CTE.
        The rest of the rule is expanded normally from the grammar node.
        """
        name_parts = self._expand_rule("ColIdOrString", depth + 1, active, {})
        name = " ".join(name_parts).strip()
        if name:
            self._ctx.cte_names.append(name)
        # Expand the remainder: InsertColumnList? UsingKey? 'AS' Materialized? CTEBody
        rule = self.grammar.rules.get("WithStatement")
        if rule is None:
            return name_parts
        node = self._cache.get(rule)
        # The full node is a SeqNode; skip the first child (ColIdOrString) we already expanded.
        if isinstance(node, SeqNode) and node.children:
            rest_node = SeqNode(children=node.children[1:])
            rest = self._expand_node(rest_node, depth, rule.parameters, active | {"WithStatement"}, {})
        else:
            rest = []
        return name_parts + rest

    def _expand_window_definition(self, depth: int, active: frozenset[str]) -> list[str]:
        """Expand WindowDefinition and register the window name in context.

        WindowDefinition <- Identifier 'AS' WindowFrameDefinition
        """
        name_parts = self._expand_rule("Identifier", depth + 1, active, {})
        name = " ".join(name_parts).strip()
        if name:
            self._ctx.window_names.append(name)
        frame_parts = self._expand_rule("WindowFrameDefinition", depth + 1, active, {})
        return name_parts + ["AS"] + frame_parts

    def _expand_window_frame(self, depth: int, active: frozenset[str]) -> list[str]:
        """Expand WindowFrame with context-aware named-window handling.

        WindowFrame <- ParensIdentifier / WindowFrameDefinition / Identifier

        The bare Identifier arm is a named-window reference (e.g. OVER w).
        - No window names registered: exclude that arm to avoid dangling refs.
        - Window names registered: if that arm is chosen, return one of the
          registered names directly (not a random identifier from the pool).
        """
        rule = self.grammar.rules.get("WindowFrame")
        if rule is None:
            return []
        node = self._cache.get(rule)
        if not isinstance(node, ChoiceNode):
            return self._expand_node(node, depth, rule.parameters, active | {"WindowFrame"}, {})

        def _is_bare_identifier(opt: SeqNode) -> bool:
            return (
                len(opt.children) == 1
                and isinstance(opt.children[0], RefNode)
                and opt.children[0].ref == "Identifier"
            )

        options = node.options
        if not self._ctx.window_names:
            # No defined windows -- exclude the named-reference arm entirely.
            filtered = [opt for opt in options if not _is_bare_identifier(opt)]
            options = filtered or options
            chosen = self.rng.choice(options)
            return self._expand_node(chosen, depth, rule.parameters, active | {"WindowFrame"}, {})

        # Window names exist -- all arms are valid, but if the bare Identifier
        # arm is chosen we must use a registered name, not a random identifier.
        chosen = self.rng.choice(options)
        if _is_bare_identifier(chosen):
            return [self.rng.choice(self._ctx.window_names)]
        return self._expand_node(chosen, depth, rule.parameters, active | {"WindowFrame"}, {})

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
                # Don't blindly pick options[0] -- it is often a recursive rule
                # (e.g. ParensExpression inside SingleExpression) that collapses
                # to () at depth limit. Find the first option that expands only
                # through terminal overrides, literals, and skippable nodes.
                chosen = self._pick_simple_option(options)
            else:
                chosen = self._weighted_choice(options)
            return self._expand_node(chosen, depth, rule_params, active, param_nodes)

        if isinstance(node, OptNode):
            if depth >= self.max_depth:
                return []
            p = max(0.0, 0.40 - 0.04 * depth)
            if self.rng.random() < p:
                return self._expand_node(node.child, depth + 1, rule_params, active, param_nodes)
            return []

        if isinstance(node, RepeatNode):
            min_count = node.min_count
            if depth >= self.max_depth:
                count = min_count
            else:
                count = min_count
                p = max(0.0, 0.40 - 0.05 * depth)
                while count < 5 and self.rng.random() < p:
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
            result = self._expand_node(func_node, depth, func_rule.parameters, active, new_param_nodes)
            # List(D) <- D (',' D)* ','?  -- the trailing comma is optional in
            # the PEG grammar but Postgres rejects it in most contexts.  Strip it
            # unconditionally to avoid spurious ERR/ERR divergences.
            if node.func_name == "List" and result and result[-1] == ",":
                result = result[:-1]
            return result

        raise ValueError(f"Unknown node type: {type(node)}")
