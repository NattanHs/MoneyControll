"""Assistente de primeiro uso: nome da conta, saldo inicial, mês de início e CDI."""
from __future__ import annotations

from . import accounts, db, finance
from . import validate as v


def needed(conn) -> bool:
    """Só aparece num banco realmente novo; quem já tem dados nunca vê o assistente."""
    if db.get_settings(conn).get("wizard_done") == "1":
        return False
    if db.get_settings(conn).get("opening_month"):
        return False
    for table in ("transactions", "fixed_items", "installments", "credit_cards", "asset_ops", "transfers", "fixed_income_moves"):
        if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
            return False
    if conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] > 1:
        return False
    row = conn.execute("SELECT opening_balance FROM accounts").fetchone()
    return not row or not row[0]


def finish(conn, d: dict) -> dict:
    cdi = v.number(d, "cdi_annual", "O CDI anual", default=float(db.get_settings(conn)["cdi_annual"]))
    if cdi > 100:
        raise ValueError("CDI anual inválido.")
    month = v.month(d, "opening_month", "o mês de início")
    acc = {
        "name": v.text(d, "account_name", "o nome da conta", maxlen=40),
        "kind": d.get("account_kind") or "corrente",
        "opening_balance": d.get("opening_balance"),
    }
    accounts.save_account(conn, acc, accounts.default_id(conn))
    db.set_setting(conn, "opening_month", month)
    db.set_setting(conn, "cdi_annual", cdi)
    db.set_setting(conn, "wizard_done", "1")
    return {"ok": True}


def skip(conn) -> dict:
    db.set_setting(conn, "wizard_done", "1")
    return {"ok": True}
