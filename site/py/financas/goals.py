"""Objetivos de poupança: quanto falta e em qual mês a meta deve ser atingida.

Previsão: falta / aporte mensal, arredondado para cima, somado ao mês atual.
Sem aporte mensal não há previsão (o app avisa em vez de inventar uma data).

Objetivo que rende (earns = 1): o saldo rende pelo % do CDI informado (CDI anual das Configurações) e a
previsão simula mês a mês, com o aporte entrando no fim de cada mês. O resultado é LÍQUIDO ESTIMADO: desconta
o IR regressivo da renda fixa pelo prazo, aplicado sobre os juros. É uma aproximação (o IR real depende da data
de cada aporte) e o CDI real varia. O rendimento só entra na previsão: o valor guardado e o patrimônio continuam
sendo o que a pessoa lançou.
"""
from __future__ import annotations

import math

from . import db
from . import validate as v
from .common import ym_add


MAX_MESES_SIMULACAO = 1200   # 100 anos: acima disso a conta sem rendimento é usada
MAX_PCT_CDI = 1000


def _taxa_mensal(cdi_anual: float, pct_cdi: float) -> float:
    """Taxa mensal equivalente a pct_cdi % do CDI anual (mesma fórmula da renda fixa, em invest.monthly_rate)."""
    return (1 + cdi_anual / 100 * pct_cdi / 100) ** (1 / 12) - 1


def _aliquota_ir(meses: int) -> float:
    """IR regressivo da renda fixa pelo prazo: 22,5% até 6 meses, 20% até 12, 17,5% até 24, 15% acima."""
    if meses <= 6:
        return 0.225
    if meses <= 12:
        return 0.20
    if meses <= 24:
        return 0.175
    return 0.15


def _meses_com_rendimento(saved: float, target: float, monthly: float, taxa: float) -> int | None:
    """Meses até o valor LÍQUIDO (principal + juros após IR) chegar à meta. None se não chegar em 100 anos."""
    saldo = principal = saved
    for t in range(1, MAX_MESES_SIMULACAO + 1):
        saldo = saldo * (1 + taxa) + monthly
        principal += monthly
        liquido = principal + (saldo - principal) * (1 - _aliquota_ir(t))
        if liquido >= target - 1e-9:
            return t
    return None


def _row(g: dict, today_ym: str, cdi: float = 0.0) -> dict:
    target, saved, monthly = float(g["target"]), float(g["saved"]), float(g["monthly"])
    remaining = round(max(target - saved, 0.0), 2)
    pct = round(min(saved / target * 100, 100.0), 1) if target > 0 else 0.0
    earns = bool(g.get("earns"))
    pct_cdi = 100.0 if g.get("pct_cdi") is None else float(g["pct_cdi"])
    out = {**g, "earns": 1 if earns else 0, "pct_cdi": pct_cdi, "remaining": remaining, "pct": pct,
           "months_left": None, "eta": None, "rate_annual": None, "months_no_yield": None}
    if earns:
        out["rate_annual"] = round(cdi * pct_cdi / 100, 2)
    if remaining <= 0:
        out["status"] = "concluido"
        return out
    sem = math.ceil(round(remaining / monthly, 6)) if monthly > 0 else None
    months = sem
    if earns:
        com = _meses_com_rendimento(saved, target, monthly, _taxa_mensal(cdi, pct_cdi))
        if com is not None:
            months = com
        out["months_no_yield"] = sem
    if months is None:
        out["status"] = "sem_aporte"
    else:
        out["months_left"] = months
        out["eta"] = ym_add(today_ym, months)
        out["status"] = "em_andamento"
    return out


def _linked_totals(conn, exclude_tx: int | None = None) -> dict:
    """Saldo movimentado por lançamentos em cada objetivo: saída soma (guardar), entrada subtrai (retirar)."""
    sql = ("SELECT goal_id, SUM(CASE WHEN kind = 'saida' THEN amount ELSE -amount END) AS total "
           "FROM transactions WHERE goal_id IS NOT NULL AND id IS NOT ? GROUP BY goal_id")
    return {r["goal_id"]: round(r["total"], 2) for r in db.rows(conn, sql, (exclude_tx,))}


def available(conn, gid: int, exclude_tx: int | None = None) -> float:
    """Quanto pode ser retirado do objetivo agora (sem contar o próprio lançamento que está sendo editado)."""
    g = conn.execute("SELECT saved FROM goals WHERE id = ?", (gid,)).fetchone()
    if g is None:
        raise ValueError("Objetivo não encontrado.")
    return round(g[0] + _linked_totals(conn, exclude_tx).get(gid, 0.0), 2)


def movements(conn, gid: int) -> list[dict]:
    return db.rows(conn, "SELECT id, date, kind, description, amount, account_id, goal_cash, category FROM transactions "
                         "WHERE goal_id = ? ORDER BY date DESC, id DESC", (gid,))


def _cdi(conn) -> float:
    try:
        return max(0.0, float(db.get_settings(conn).get("cdi_annual", 0) or 0))
    except (TypeError, ValueError):
        return 0.0


def list_goals(conn, today: str) -> list[dict]:
    rows = db.rows(conn, "SELECT * FROM goals ORDER BY done, name COLLATE NOCASE")
    linked = _linked_totals(conn)
    cdi = _cdi(conn)
    out = []
    for g in rows:
        base = float(g["saved"])
        extra = linked.get(g["id"], 0.0)
        out.append(_row({**g, "saved": round(base + extra, 2), "saved_base": base, "saved_linked": extra}, today[:7], cdi))
    return out


def goal_options(conn) -> list[dict]:
    return db.rows(conn, "SELECT id, name FROM goals ORDER BY name COLLATE NOCASE")


def check_goal(conn, value) -> int | None:
    """Objetivo informado num lançamento (None/vazio = nenhum)."""
    if value in (None, "", 0, "0"):
        return None
    try:
        gid = int(value)
    except (TypeError, ValueError):
        raise ValueError("Objetivo inválido.")
    if conn.execute("SELECT 1 FROM goals WHERE id = ?", (gid,)).fetchone() is None:
        raise ValueError("Objetivo não encontrado.")
    return gid


def save_goal(conn, d: dict, id: int | None = None) -> int:
    row = {
        "name": v.text(d, "name", "o nome do objetivo", maxlen=60),
        "target": round(v.number(d, "target", "O valor do objetivo"), 2),
        "saved": round(v.number(d, "saved", "O valor já guardado", allow_zero=True, default=0), 2),
        "monthly": round(v.number(d, "monthly", "O quanto guardar por mês", allow_zero=True, default=0), 2),
    }
    if "reserve" in d or id is None:  # editar pelo formulário de objetivo não tira a marca de reserva
        row["reserve"] = 1 if d.get("reserve") else 0
    if "earns" in d or id is None:  # quem não envia a caixa (ex.: a reserva sugerida) não muda o rendimento
        row["earns"] = v.boolean(d, "earns", False)
        if row["earns"]:
            raw = d.get("pct_cdi")
            row["pct_cdi"] = 100.0 if raw in (None,) else round(v.number(d, "pct_cdi", "O percentual do CDI"), 2)
            if row["pct_cdi"] <= 0:
                raise ValueError("O percentual do CDI deve ser maior que zero.")
            if row["pct_cdi"] > MAX_PCT_CDI:
                raise ValueError("Percentual do CDI muito alto.")
    return _save(conn, row, id)


def _save(conn, row: dict, id: int | None) -> int:
    cols = list(row)
    if id is None:
        cur = conn.execute(f"INSERT INTO goals ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                           [row[c] for c in cols])
        return cur.lastrowid
    cur = conn.execute(f"UPDATE goals SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?",
                       [row[c] for c in cols] + [id])
    if cur.rowcount == 0:
        raise ValueError("Registro não encontrado.")
    return id


def delete_goal(conn, id: int) -> None:
    linked = conn.execute("SELECT COUNT(*) FROM transactions WHERE goal_id = ?", (id,)).fetchone()[0]
    if linked:
        raise ValueError(f"Este objetivo tem {linked} lançamento(s) vinculado(s). Exclua ou desvincule-os antes.")
    if conn.execute("DELETE FROM goals WHERE id = ?", (id,)).rowcount == 0:
        raise ValueError("Registro não encontrado.")
