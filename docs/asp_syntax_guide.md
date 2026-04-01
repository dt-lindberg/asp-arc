---
name: ASP Syntax Guide
description: Condensed reference of common Clingo syntax errors and correct alternatives. Injected into the syntax-agent system prompt at load time.
---

# Common Clingo Syntax Errors

1. **No `aggregate/3`** — Use `N = #count { C : pred(C) }`, not `aggregate(N = #count, pred(C), N)`. The `aggregate/3` predicate is SWI-Prolog; it does not exist in Clingo.

2. **No `#mod`** — Use `\` for modulo: `C \ 2 = 0`, not `C #mod 2 = 0`. Clingo follows ISO Prolog arithmetic.

3. **Aggregate must be on the right-hand side** — Write `Var = #min { R : p(R) }`, not `#min { R : p(R) } = Var`. Clingo evaluates `Var = #aggr{...}`, not the reverse.

4. **`#const` names must be lowercase** — Write `#const max_dim = 30.`, not `#const MAX = 30.`. An uppercase token is parsed as a variable, which is invalid in a `#const` directive.

5. **No `exactly N` suffix on cardinality** — Write `1 { output(R,C,Col) : color(Col) } 1 :- cell(R,C).`, not `{ ... } exactly 1 :- cell(R,C).`. Clingo uses prefix/suffix integer bounds.

6. **Unsafe variables under negation** — Every variable must appear in at least one positive body literal. Add a positive binder before negating: `missing(R) :- row(R), col(C), not input(R,C,5).`, not `missing(R) :- col(C), not input(R,C,5).` (here `R` is unsafe).

7. **No predicate names in arithmetic** — `row_min` is a predicate, not an integer. Extract the value first: `border(R) :- row_min(Rmin), R = Rmin - 1.`, not `border(R) :- R = row_min - 1.`

8. **No top-level variable assignment** — Aggregates must appear in a rule with a head: `min_row(Rmin) :- Rmin = #min { R : p(R) }.`, not the standalone `MinR = #min { R : p(R) }.`
