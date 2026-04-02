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

9. **No `succ/2`, `plus/3`, `msort/2`** — These are SWI-Prolog. Use arithmetic directly: `Next = N + 1`, `Sum = A + B`. There is no sort predicate in Clingo.

10. **`#count` body needs curly braces with colon** — Write `N = #count { X : pred(X) }`, not `N = #count(pred(X))`. The aggregate body uses `{ Vars : condition }` syntax.

11. **Aggregate variables must be grounded** — In `N = #count { R,C : input(R,C,_) }`, the grounding set must be finite. All variables in the tuple must appear positively in the condition.

12. **Range syntax uses `..` not `to`** — Write `R = 0..H-1`, not `R = 0 to H-1`. Range expressions always use double dot.

13. **No `assert/retract`** — Clingo programs are static. There is no assert or retract. All rules are declared upfront.

14. **Conditional literals need correct position** — `output(R,C,V) : input(R,C,V)` is a conditional literal, valid inside aggregates or choice heads. Not valid as a standalone rule body.

15. **Head disjunction uses `;`** — `a ; b :- body.` means either a or b (or both) must hold. This is different from `1 { a ; b } 1 :- body.` which is a choice with exactly one.

16. **No inline aggregate-to-aggregate comparison** — You cannot compare two aggregates directly in a rule body. Bind at least one to a variable first: `pivot(P) :- col(P), N = #count{R : row(R)}, N = #count{R : input(R,P,5)}.`, not `pivot(P) :- col(P), #count{R : input(R,P,5)} = #count{R : row(R)}.`. Alternatively, avoid the aggregate entirely with negation-as-failure: `bad(P) :- col(P), row(R), not input(R,P,5). pivot(P) :- col(P), not bad(P).`

# Correct Pattern Reference

```asp
% Computing grid dimensions
height(H) :- H = 1 + #max { R : input(R,_,_) }.
width(W)  :- W = 1 + #max { C : input(_,C,_) }.

% Domain for output cells (same size as input)
output_cell(R,C) :- height(H), width(W), R = 0..H-1, C = 0..W-1.

% Choice rule (exactly one color per cell)
1 { output(R,C,Col) : color(Col) } 1 :- output_cell(R,C).

% Count non-zero cells in a row
nonzero_count(R, N) :- N = #count { C : input(R,C,V), V != 0 }, input(R,_,_).

% Minimum row containing color 5
min_row_5(Rmin) :- Rmin = #min { R : input(R,_,5) }.

% Safe negation (bind R and C positively first)
empty(R,C) :- output_cell(R,C), not input(R,C,_).

% Modulo arithmetic
even_col(C) :- output_cell(_,C), C \ 2 = 0.

% Using a computed value in arithmetic
right_edge(R,C) :- width(W), output_cell(R,C), C = W - 1.
```
