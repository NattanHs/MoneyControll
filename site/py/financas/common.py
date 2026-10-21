"""Funções de data (meses "AAAA-MM") e de valores com reajuste, compartilhadas pelos módulos."""
from __future__ import annotations

import calendar


def ym_add(ym: str, n: int) -> str:
    idx = int(ym[:4]) * 12 + (int(ym[5:7]) - 1) + n
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def ym_diff(a: str, b: str) -> int:
    """Quantos meses de a até b (negativo se b vier antes)."""
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])


def month_day(ym: str, day: int) -> str:
    last = calendar.monthrange(int(ym[:4]), int(ym[5:7]))[1]
    return f"{ym}-{min(day, last):02d}"


def amount_at(base: float, changes: list[dict], ym: str) -> float:
    """Valor vigente em ym: o último reajuste com from_month <= ym (ou o valor original)."""
    value = base
    for c in sorted(changes, key=lambda c: c["from_month"]):
        if c["from_month"] <= ym:
            value = c["amount"]
        else:
            break
    return value
