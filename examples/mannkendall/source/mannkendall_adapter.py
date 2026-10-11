"""Typed JSON adapters for the unchanged pyMannKendall v1.4.3 scientific module.

All statistical computation is delegated to the pinned upstream public API.
See README.md for assumptions, field meanings, units, and source provenance.
"""

import math

import pymannkendall as upstream


def _validate_input(x: list[float | None], alpha: float) -> None:
    if not isinstance(x, list):
        raise ValueError("x must be a one-dimensional list of finite numbers or null.")
    for value in x:
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("x must contain only finite numbers or null; booleans are not numbers.")
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError("x must contain finite numbers; use null for a missing observation.")
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0 < alpha < 1:
        raise ValueError("alpha must be a finite number strictly between 0 and 1.")


def original_trend(x: list[float | None], alpha: float = 0.05) -> dict:
    """Test an ordered series with original Mann-Kendall; serial correlation/seasonality are not corrected.
    Input: finite numbers/null preserving time positions and alpha; output: all nine native fields, slope per observation step.
    """
    _validate_input(x, alpha)
    if sum(value is not None for value in x) < 2:
        raise ValueError("original_trend requires at least two non-missing observations.")
    return upstream.original_test(x, alpha=alpha)._asdict()


def seasonal_trend(x: list[float | None], period: int = 12, alpha: float = 0.05) -> dict:
    """Test an ordered series with seasonal Mann-Kendall; period defines recurring seasonal positions.
    Input: finite numbers/null preserving positions, positive integer period and alpha; output: nine native fields, slope per complete cycle.
    """
    _validate_input(x, alpha)
    if isinstance(period, bool) or not isinstance(period, int) or period <= 0:
        raise ValueError("period must be a positive integer.")
    seen_phases = set()
    has_pair = False
    for index, value in enumerate(x):
        if value is None:
            continue
        phase = index % period
        if phase in seen_phases:
            has_pair = True
            break
        seen_phases.add(phase)
    if not has_pair:
        raise ValueError("seasonal_trend requires two non-missing observations at the same seasonal position.")
    return upstream.seasonal_test(x, period=period, alpha=alpha)._asdict()
