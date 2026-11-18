"""Timing features: H, DD, UD (in ms).

H_i  = release(i) - press(i)        i = 1..n
DD_i = press(i+1) - press(i)        i = 1..n-1
UD_i = press(i+1) - release(i)      i = 1..n-1   (can be negative)

Vector order (fixed for a given text):
    [H_1..H_n, DD_1..DD_{n-1}, UD_1..UD_{n-1}]   -> length 3n-2
"""
from __future__ import annotations

from .schemas import pair_events


def extract_features(events: list[dict], text: str) -> list[float]:
    pairs = pair_events(events, text)
    press = [p[0] for p in pairs]
    release = [p[1] for p in pairs]
    n = len(pairs)
    h = [release[i] - press[i] for i in range(n)]
    dd = [press[i + 1] - press[i] for i in range(n - 1)]
    ud = [press[i + 1] - release[i] for i in range(n - 1)]
    return h + dd + ud


def feature_names(text: str) -> list[str]:
    n = len(text)
    return ([f"H_{i + 1}" for i in range(n)]
            + [f"DD_{i + 1}" for i in range(n - 1)]
            + [f"UD_{i + 1}" for i in range(n - 1)])
