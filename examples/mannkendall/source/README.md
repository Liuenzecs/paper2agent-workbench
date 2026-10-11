# pyMannKendall typed adapters for Paper2Agent Workbench

This directory gives the generic Workbench two JSON-typed entry points for existing, unchanged statistical code. It contains no models and needs no GPU. The Workbench generates and decorates the MCP server; this module itself has no FastMCP dependency.

## Provenance and reuse

- Paper: Hussain and Mahmud, **pyMannKendall: a python package for non parametric Mann Kendall family of trend tests**, JOSS (2019), DOI [10.21105/joss.01556](https://joss.theoj.org/papers/10.21105/joss.01556).
- Repository: [mmhs013/pyMannKendall](https://github.com/mmhs013/pyMannKendall), tag **v1.4.3**, commit **1ff32de4c4f4b50e1d922763ae88b3cb677c52b0**.
- `pymannkendall.py` is a byte-for-byte copy of the formally downloaded Windows checkout's `pymannkendall/pymannkendall.py`. Only its directory location changed. Checkout SHA-256: **537e3b1234a0de0297223729c8aa079978e0ec3d77401c5b8592794e512d5fd5**. The upstream/preflight LF-normalized SHA-256 is **ef69c2ffd73552d9f398d81843893c8cc2942c48ee889e7f67c75fe3c843ad17**; Git checkout line endings account for the byte difference.
- The upstream MIT license is retained in `LICENSE`. The publication's archived release was v1.1; this demo uses the later v1.4.3 maintenance version and does not claim byte identity with the 2019 archive.
- `original_trend` calls `upstream.original_test(x, alpha=alpha)` exactly once. `seasonal_trend` calls `upstream.seasonal_test(x, period=period, alpha=alpha)` exactly once. Both convert the returned namedtuple using `_asdict()`. The adapter does not calculate statistical formulas, repeat the test, or modify the input.

## Input contract

Supply `x` as a time-ordered one-dimensional JSON array of finite numbers or `null`. The upstream routine handles `null` as NaN. **Keep missing observations in their original positions**: removing them changes slope spacing and seasonal alignment. Strings, booleans, nested arrays, NaN and Infinity are rejected. Original MK needs at least two non-missing observations. Seasonal MK needs at least one recurring seasonal position with two non-missing observations; incomplete final cycles are permitted and handled by upstream padding.

`alpha` defaults to **0.05** and must be strictly between 0 and 1. Seasonal `period` defaults to **12** and must be a positive integer. These preserve upstream defaults. A period of 12 is a software default, not a claim that the article proves a particular dataset is monthly. Give `period` explicitly for the actual sampling cycle.

Original MK does **not** correct serial correlation or seasonality. Seasonal MK compares recurring seasonal positions; it does not add an autocorrelation correction. Observations must be ordered and appropriately aligned with the selected seasonal cycle; users must assess the statistical assumptions for their own data. Other upstream autocorrelation, regional, multivariate and partial tests are outside this two-tool demo. The Sen slope functions are already called by the selected tests and are not exposed again.

## Output

All nine native fields are preserved:

| Field | Meaning |
| --- | --- |
| `trend` | `increasing`, `decreasing`, or `no trend` according to upstream significance decision |
| `h` | Boolean significance decision |
| `p` | Native significance-test p-value |
| `z` | Normalized MK statistic |
| `Tau` | Native Kendall Tau |
| `s` | MK score |
| `var_s` | Score variance |
| `slope` | Sen slope; per observation-index step for original MK, per complete `period` cycle for seasonal MK |
| `intercept` | Native Kendall-Theil robust-line intercept, in observation-value units |

The generic MCP server performs JSON conversion of NumPy scalar values and creates a fresh result artifact per invocation. No scientific value is rounded by the adapter. Extremely scaled finite input can still overflow upstream floating-point arithmetic; no replacement numerical method is introduced.

## Verification scope

The separate run records compare all nine fields against direct upstream execution, including changed alpha, changed period, reversed data, missing positions and incomplete cycles. The official 360-value `arbitrary_1d_data` fixture is **an arbitrary upstream test array**, not a verified real rainfall dataset. Synthetic trend/constant tests establish software properties, not empirical claims from the paper. The entire package test suite and full scientific paper reproduction are outside this demo.

Only NumPy 2.4.6 and SciPy 1.17.1 are scientific runtime dependencies. FastMCP and the generated server requirements are supplied by the Workbench. The original seasonal docstring's positional example `seasonal_test(x, 0.05)` would put 0.05 in `period`; this adapter passes both optional parameters by keyword.

## MCP input validation boundary

Workbench 0.3.2 sets `strict_input_validation=True` in every newly generated server. No environment setting is needed. JSON booleans and numeric strings are rejected before invocation; integer JSON numbers remain valid float inputs. Domain checks are retained in the adapter. Existing generated projects must be regenerated; historical files are not edited.
