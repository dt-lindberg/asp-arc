# Observed Syntax Errors in LLM-Generated ASP

This document catalogues recurring Clingo syntax and safety errors observed across pipeline runs on puzzles `8d510a79`, `39e1d7f9`, and `8a004b2b`. Each section describes what the model produces, why it is wrong, and what the model should write instead.

---

## 1. Non-Clingo aggregate syntax: `aggregate(...)`

**Observed:**
```asp
row_five_count(R, N) :- aggregate(N = #count, input(R,_,5), N).
```

**Error:** `syntax error, unexpected =, expecting ) or ;`

**What's wrong:** `aggregate/3` is a SWI-Prolog built-in. Clingo has no such predicate. The model appears to conflate Prolog and ASP syntax when computing counts or sums.

**What to write instead:**
```asp
row_five_count(R, N) :- N = #count { C : input(R,C,5) }.
```

---

## 2. Wrong modulo operator: `#mod`

**Observed:**
```asp
even_col(C) :- color(C), C #mod 2 = 0.
```

**Error:** `lexer error, unexpected #mod`

**What's wrong:** `#mod` does not exist in Clingo. Arithmetic uses the `\` operator for modulo (following ISO Prolog / Clingo conventions).

**What to write instead:**
```asp
even_col(C) :- color(C), C \ 2 = 0.
```

---

## 3. Inverted aggregate assignment

**Observed:**
```asp
row_min(Rmin) :- #min { R : row_exists(R) } = Rmin.
filler(Fcol)  :- #min { F : candidate(F) } = F.
```

**Error:** `syntax error, unexpected =, expecting ) or ;`

**What's wrong:** The aggregate must appear on the right-hand side of the `=`. Clingo evaluates `Var = #aggr{...}`, not the reverse.

**What to write instead:**
```asp
row_min(Rmin) :- Rmin = #min { R : row_exists(R) }.
filler(Fcol)  :- Fcol = #min { F : candidate(F) }.
```

---

## 4. Uppercase `#const` name

**Observed:**
```asp
#const MAX = 30.
```

**Error:** `syntax error, unexpected <VARIABLE>, expecting <IDENTIFIER> or default or override`

**What's wrong:** In Clingo, `#const` names must be lowercase identifiers. An uppercase token is parsed as a variable, which is not valid in this context.

**What to write instead:**
```asp
#const max_dim = 30.
```

---

## 5. Cardinality constraint suffix form: `{ } exactly 1`

**Observed:**
```asp
{ output(R,C,Col) : color(Col) } exactly 1 :- cell(R,C).
```

**Error:** `syntax error, unexpected <IDENTIFIER>, expecting )`

**What's wrong:** Clingo does not support the `exactly N` suffix. Cardinality bounds go as a prefix lower bound and suffix upper bound on the aggregate, using the `L { ... } U` form.

**What to write instead:**
```asp
1 { output(R,C,Col) : color(Col) } 1 :- cell(R,C).
```

---

## 6. Unsafe variables under negation-as-failure

**Observed:**
```asp
missing_five(R) :- col(C), not input(R, C, 5).

:- pattern(R1,R2,C1,C2,Col), R >= R1, R <= R2, C >= C1, C <= C2,
   not cell_exists(R,C).
```

**Error:** `unsafe variables in: ...`

**What's wrong:** Variables that appear only inside a `not` literal (or only in a comparison with another unsafe variable) are not grounded. Clingo requires every variable in a rule to appear in at least one positive body literal.

**What to write instead** — bind `R` and `C` positively before negating:
```asp
missing_five(R) :- row(R), col(C), not input(R, C, 5).

:- pattern(R1,R2,C1,C2,Col), row(R), col(C),
   R >= R1, R <= R2, C >= C1, C <= C2,
   not cell_exists(R,C).
```

---

## 7. Treating predicates as integer constants in arithmetic

**Observed:**
```asp
border(R,C) :- R = row_min - 1.
```

**Error:** `syntax error, unexpected =, expecting )`

**What's wrong:** `row_min` is a predicate name, not an integer. It cannot be used in an arithmetic expression. To use a computed minimum in arithmetic, the value must first be extracted via a fact.

**What to write instead:**
```asp
row_min(Rmin) :- Rmin = #min { R : input(R,_,_) }.
border(R,C)   :- row_min(Rmin), R = Rmin - 1.
```

---

## 8. Top-level global aggregate assignment

**Observed:**
```asp
MinR = #min { R : four(R,_) }.
MaxR = #max { R : four(R,_) }.
```

**Error:** `syntax error` or grounding error

**What's wrong:** ASP has no notion of top-level variable assignment. `MinR` here is an unbound variable floating in the body of an implicit rule with no head — this is not valid. Aggregates must appear in a rule body alongside a head predicate.

**What to write instead:**
```asp
min_row(Rmin) :- Rmin = #min { R : four(R,_) }.
max_row(Rmax) :- Rmax = #max { R : four(R,_) }.
```

---

## Summary table

| Error | Clingo error type | Root cause |
|---|---|---|
| `aggregate(N = #count, ...)` | syntax error | SWI-Prolog built-in, not Clingo |
| `C #mod 2` | lexer error | Wrong modulo operator; use `\` |
| `#min{...} = Var` | syntax error | Aggregate must be on the RHS: `Var = #min{...}` |
| `#const MAX = 30` | syntax error | `#const` names must be lowercase |
| `{ } exactly 1` | syntax error | Use `1 { } 1` prefix/suffix form |
| Unsafe variable under `not` | unsafe variables | Bind variables positively before negating |
| `predicate_name - 1` in arithmetic | syntax error | Extract value via a fact first |
| Top-level `Var = #aggr{...}.` | syntax/ground error | Must appear in a rule with a head |
