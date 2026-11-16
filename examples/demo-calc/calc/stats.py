"""Tiny statistics helpers used as the harness demo target."""


def mean(values):
    if not values:
        raise ValueError("mean() of empty sequence")
    return sum(values) / len(values)


def median(values):
    s = sorted(values)
    n = len(s)
    return s[n // 2]
