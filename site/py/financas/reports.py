"""Telas que combinam caixa e investimentos: visão geral, painel de investimentos e configurações."""
from __future__ import annotations

from . import accounts, db, finance, invest
from . import validate as v

KIND_PLURAL = {"acao": "Ações", "fii": "FIIs", "etf": "ETFs", "bdr": "BDRs", "outro": "Outros"}


def get_settings(conn) -> dict:
    s = db.get_settings(conn)
    total = conn.execute("SELECT COALESCE(SUM(opening_balance), 0) FROM accounts").fetchone()[0]
    return {"cdi_annual": float(s["cdi_annual"]), "opening_balance": round(float(total), 2),
            "opening_month": s["opening_month"]}


def save_settings(conn, d: dict) -> dict:
    cdi = v.number(d, "cdi_annual", "O CDI anual")
    if cdi > 100:
        raise ValueError("CDI anual inválido.")
    month = v.month(d, "opening_month", required=False) or ""
    if "opening_balance" in d:  # compatibilidade: o saldo inicial agora é de cada conta; aqui vale para a conta padrão
        if conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1:
            balance = round(v.number(d, "opening_balance", "O saldo inicial", allow_zero=True, allow_negative=True, default=0), 2)
            conn.execute("UPDATE accounts SET opening_balance = ?", (balance,))
    db.set_setting(conn, "cdi_annual", cdi)
    db.set_setting(conn, "opening_month", month)
    return get_settings(conn)


def _last_months(ym: str, n: int) -> list[str]:
    return [finance.ym_add(ym, -i) for i in range(n - 1, -1, -1)]


def _allocation(positions: list[dict], products: list[dict]) -> list[dict]:
    items = []
    by_kind: dict[str, float] = {}
    for p in positions:
        if not p["closed"]:
            by_kind[p["kind"]] = by_kind.get(p["kind"], 0.0) + p["value"]
    for kind, value in by_kind.items():
        items.append({"label": KIND_PLURAL.get(kind, kind), "value": round(value, 2)})
    rf = sum(p["saldo"] for p in products)
    if rf > 0:
        items.append({"label": "Renda fixa (CDI)", "value": round(rf, 2)})
    return items


def overview(conn, today: str) -> dict:
    ym = today[:7]
    data = finance.load_all(conn)
    months = {m["ym"]: m for m in finance.run(data, finance.ym_add(ym, 3), today)}
    current = months.get(ym, finance.zero_month(ym))
    cdi = float(data["settings"]["cdi_annual"])
    positions = invest.positions(conn, today)
    products = invest.fi_products(conn, today, cdi)
    rv_value = round(sum(p["value"] for p in positions if not p["closed"]), 2)
    rf_value = round(sum(p["saldo"] for p in products), 2)
    conta = current["saldo_final"]

    labels = _last_months(ym, 12)
    inv = invest.series(conn, labels)
    inv[-1].update({"rf": rf_value, "rv": rv_value, "total": round(rf_value + rv_value, 2)})
    evolution = [{"ym": m, "conta": months[m]["saldo_final"] if m in months else 0.0,
                  "rf": i["rf"], "rv": i["rv"]} for m, i in zip(labels, inv)]

    allocation = _allocation(positions, products)
    if conta > 0:
        allocation.insert(0, {"label": "Conta", "value": conta})
    budget_alerts = [b for b in finance.budget_status(
        data["budgets"], finance.spending_by_category(finance.items_for_month(data, ym, today)))
        if b["state"] != "ok"]
    upcoming = [months[m] for m in (finance.ym_add(ym, k) for k in (1, 2, 3)) if m in months]
    acc = accounts.list_accounts(conn, today)
    return {
        "today": today, "ym": ym, "accounts": acc["items"],
        "patrimonio_total": round(conta + rv_value + rf_value, 2),
        "conta": conta, "investido": round(rv_value + rf_value, 2),
        "renda_variavel": rv_value, "renda_fixa": rf_value,
        "current_month": current, "evolution": evolution, "allocation": allocation, "upcoming": upcoming,
        "budget_alerts": budget_alerts,
        "has_data": bool(data["transactions"] or data["fixed"] or data["installments"] or data["transfers"]
                         or data["ops"] or data["fi_moves"]),
    }


def invest_dashboard(conn, today: str) -> dict:
    ym = today[:7]
    cdi = float(db.get_settings(conn)["cdi_annual"])
    positions = invest.positions(conn, today)
    products = invest.fi_products(conn, today, cdi)
    active = [p for p in positions if not p["closed"]]
    rv_cost = round(sum(p["cost"] for p in active), 2)
    rv_value = round(sum(p["value"] for p in active), 2)
    div12 = round(sum(p["div_12m"] for p in active), 2)
    rf_value = round(sum(p["saldo"] for p in products), 2)

    dates = [r["date"] for r in db.rows(conn, "SELECT date FROM asset_ops")]
    dates += [r["date"] for r in db.rows(conn, "SELECT date FROM fixed_income_moves")]
    first = min(dates)[:7] if dates else ym
    span = min(24, finance.ym_diff(first, ym) + 1)
    labels = _last_months(ym, max(span, 2))
    evolution = invest.series(conn, labels)
    evolution[-1].update({"rf": rf_value, "rv": rv_value, "total": round(rf_value + rv_value, 2)})

    return {
        "today": today, "cdi_annual": cdi,
        "totals": {
            "total": round(rv_value + rf_value, 2), "rv_cost": rv_cost, "rv_value": rv_value, "rf_value": rf_value,
            "div_12m": div12,
            "yoc_portfolio": round(div12 / rv_cost * 100, 2) if rv_cost > 0 else None,
            "realized": round(sum(p["realized"] for p in positions), 2),
        },
        "positions": positions, "fixed_income": products,
        "evolution": evolution, "allocation": _allocation(positions, products),
        "projection": invest.projection(conn, today, cdi),
    }
