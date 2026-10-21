"""Validação e leitura de campos vindos da interface."""
from __future__ import annotations

import math
import re
from datetime import date


def parse_br(raw: str) -> float:
    """Aceita '1.234,56', '1234,56', '1234.56', 'R$ 12,5'."""
    s = str(raw).replace("R$", "").replace(" ", "").strip()
    if not s:
        raise ValueError("vazio")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"[1-9]\d{0,2}(\.\d{3})+", s):  # 1.234 ou 1.234.567 = milhar
        s = s.replace(".", "")
    return float(s)


def text(d: dict, key: str, label: str, required: bool = True, maxlen: int = 120) -> str:
    value = str(d.get(key) or "").strip()
    if required and not value:
        raise ValueError(f"Informe {label}.")
    if len(value) > maxlen:
        raise ValueError(f"{label} está muito longo (máximo {maxlen} caracteres).")
    return value


def number(d: dict, key: str, label: str, *, allow_zero: bool = False, allow_negative: bool = False,
           default=None) -> float:
    raw = d.get(key, default)
    if raw is None or raw == "":
        raise ValueError(f"Informe {label}.")
    try:
        value = parse_br(raw) if isinstance(raw, str) else float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{label} inválido.") from None
    if not math.isfinite(value):
        raise ValueError(f"{label} inválido.")
    if not allow_negative:
        if value < 0 or (value == 0 and not allow_zero):
            raise ValueError(f"{label} deve ser maior que zero.")
    return value


def integer(d: dict, key: str, label: str, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        value = int(float(d.get(key)))
    except (TypeError, ValueError):
        raise ValueError(f"{label} inválido.") from None
    if value < minimum or (maximum is not None and value > maximum):
        faixa = f"entre {minimum} e {maximum}" if maximum is not None else f"no mínimo {minimum}"
        raise ValueError(f"{label} deve ser {faixa}.")
    return value


def iso_date(d: dict, key: str, label: str = "a data") -> str:
    raw = str(d.get(key) or "").strip()
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"Data inválida ({label}).") from None
    if not 1990 <= parsed.year <= 2100:
        raise ValueError(f"Data fora do intervalo permitido ({label}).")
    return parsed.isoformat()


def month(d: dict, key: str, label: str = "o mês", required: bool = True) -> str | None:
    raw = str(d.get(key) or "").strip()
    if not raw:
        if required:
            raise ValueError(f"Informe {label}.")
        return None
    check_month(raw, label)
    return raw


def check_month(raw: str, label: str = "mês") -> str:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", raw) or not 1990 <= int(raw[:4]) <= 2100:
        raise ValueError(f"Mês inválido ({label}).")
    return raw


def choice(d: dict, key: str, label: str, options) -> str:
    value = str(d.get(key) or "").strip()
    if value not in options:
        raise ValueError(f"{label} inválido.")
    return value


def boolean(d: dict, key: str, default: bool = True) -> int:
    value = d.get(key, default)
    if isinstance(value, str):
        value = value.lower() in ("1", "true", "sim", "on")
    return 1 if value else 0
