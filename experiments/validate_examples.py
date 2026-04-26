"""Validate candidate worked examples against Clingo on synthetic input grids."""

from clingo.control import Control


def run(prog, facts):
    msgs = []

    def _logger(code, message):
        msgs.append(f"{code.name}: {message.strip()}")

    ctl = Control(["10", "--warn=all"], logger=_logger)
    full = facts + "\n" + prog
    try:
        ctl.add("base", [], full)
    except RuntimeError as e:
        return None, f"PARSE: {e}", msgs
    try:
        ctl.ground([("base", [])])
    except RuntimeError as e:
        return None, f"GROUND: {e}", msgs

    models = []
    on_model = lambda m: models.append(sorted(str(a) for a in m.symbols(atoms=True) if str(a).startswith("output(")))
    res = ctl.solve(on_model=on_model)
    return models, f"sat={res.satisfiable} n_models={len(models)}", msgs


# Example 2: replace every non-zero cell with color 5; zero cells stay.
ex2 = """
% Grid dimensions
n_rows(N) :- N = #count{ R : input(R, _, _) }.
n_cols(N) :- N = #count{ C : input(_, C, _) }.
row(0..N-1) :- n_rows(N).
col(0..M-1) :- n_cols(M).

output_cell(R, C) :- row(R), col(C).

1 { output(R, C, Color) : color(Color) } 1 :- output_cell(R, C).

% Recolor: every non-zero input cell becomes 5; zero cells stay 0.
output(R, C, 5) :- input(R, C, V), V != 0.
output(R, C, 0) :- input(R, C, 0).

#show output/3.
"""

facts2 = """
color(0..9).
input(0,0,0). input(0,1,1). input(0,2,0).
input(1,0,2). input(1,1,0). input(1,2,3).
input(2,0,0). input(2,1,4). input(2,2,0).
"""

# Example 3: horizontal flip — output(R, C') = input(R, M-1-C).
ex3 = """
% Grid dimensions
n_rows(N) :- N = #count{ R : input(R, _, _) }.
n_cols(N) :- N = #count{ C : input(_, C, _) }.
row(0..N-1) :- n_rows(N).
col(0..M-1) :- n_cols(M).

output_cell(R, C) :- row(R), col(C).

1 { output(R, C, Color) : color(Color) } 1 :- output_cell(R, C).

% Mirror left-to-right: column C in output comes from column M-1-C in input.
output(R, C, Color) :- input(R, Cin, Color), n_cols(M), C = M - 1 - Cin.

#show output/3.
"""

facts3 = """
color(0..9).
input(0,0,1). input(0,1,2). input(0,2,3).
input(1,0,4). input(1,1,5). input(1,2,6).
"""

for name, prog, facts in [("ex2-recolor", ex2, facts2), ("ex3-flip", ex3, facts3)]:
    models, summary, msgs = run(prog, facts)
    print(f"\n=== {name} === {summary}")
    for m in msgs:
        print(f"  msg: {m}")
    if models:
        for i, mdl in enumerate(models[:2]):
            print(f"  model {i}: {mdl}")
