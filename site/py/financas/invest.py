"""Investimentos: ações/FIIs (preço médio, proventos, yield on cost) e renda fixa em % do CDI."""
from __future__ import annotations

import re
from datetime import date, timedelta

from . import accounts, db, finance
from . import validate as v

ASSET_KINDS = {"acao": "Ação", "fii": "FII", "etf": "ETF", "bdr": "BDR", "outro": "Outro"}
DIVIDEND_TYPES = ("Dividendo", "JCP", "Rendimento")
FI_MOVE_KINDS = ("aporte", "resgate", "rendimento")
TAX_REGIMES = {"regressivo": "IR regressivo (CDB, Tesouro, LC, fundos)", "isento": "Isento (LCI, LCA, poupança)"}


def clean_ticker(raw) -> str:
    ticker = str(raw or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9.\-]{2,12}", ticker):
        raise ValueError("Código do ativo inválido (exemplos: PETR4, HGLG11).")
    return ticker


def _group(rows: list[dict], key: str) -> dict:
    grouped: dict = {}
    for r in rows:
        grouped.setdefault(r[key], []).append(r)
    return grouped


# ================================================================ ações / FIIs

def replay(ops: list[dict]) -> dict:
    """Reprocessa as operações em ordem cronológica.

    Compra: o custo total soma (quantidade × preço + taxas) e o preço médio é recalculado
    sobre o total acumulado. Venda: reduz a quantidade ao preço médio vigente (o preço médio
    não muda) e apura o lucro realizado.
    """
    qty = cost = realized = 0.0
    for o in sorted(ops, key=lambda o: (o["date"], o["op"] == "venda", o.get("id") or 0)):
        q, fees = o["quantity"], o.get("fees") or 0.0
        gross = q * o["price"]
        if o["op"] == "compra":
            cost += gross + fees
            qty += q
        else:
            if q > qty + 1e-9:
                raise ValueError(f"A venda de {o['date'][8:10]}/{o['date'][5:7]}/{o['date'][:4]} "
                                 "ultrapassa a quantidade que você tinha na carteira.")
            avg = cost / qty if qty else 0.0
            cost -= avg * q
            qty -= q
            realized += (gross - fees) - avg * q
            if qty < 1e-9:
                qty = cost = 0.0
    return {"qty": qty, "cost": cost, "avg": cost / qty if qty else 0.0, "realized": realized}


def _asset_ops(conn, asset_id: int) -> list[dict]:
    return db.rows(conn, "SELECT * FROM asset_ops WHERE asset_id = ?", (asset_id,))


def save_op(conn, d: dict, id: int | None = None) -> int:
    ticker = clean_ticker(d.get("ticker"))
    op = v.choice(d, "op", "A operação", ("compra", "venda"))
    row = {
        "date": v.iso_date(d, "date"),
        "op": op,
        "quantity": v.number(d, "quantity", "A quantidade"),
        "price": v.number(d, "price", "O preço"),
        "fees": v.number(d, "fees", "As taxas", allow_zero=True, default=0),
        "affects_cash": v.boolean(d, "affects_cash", True),
        "account_id": accounts.check_account(conn, d.get("account_id")),
    }
    old = None
    if id is not None:
        old = conn.execute("SELECT * FROM asset_ops WHERE id = ?", (id,)).fetchone()
        if old is None:
            raise ValueError("Operação não encontrada.")
    asset = conn.execute("SELECT * FROM assets WHERE ticker = ?", (ticker,)).fetchone()
    if asset is None:
        if op == "venda":
            raise ValueError(f"Você não possui {ticker} na carteira.")
        kind = v.choice(d, "kind", "O tipo do ativo", ASSET_KINDS)
        asset_id = conn.execute("INSERT INTO assets(ticker, kind) VALUES (?, ?)", (ticker, kind)).lastrowid
    else:
        asset_id = asset["id"]
        others = [o for o in _asset_ops(conn, asset_id) if o["id"] != id]
        replay(others + [dict(row, id=id or 10 ** 12)])  # valida (ex.: venda sem saldo)
    moved = old is not None and old["asset_id"] != asset_id
    if moved:
        try:
            replay([o for o in _asset_ops(conn, old["asset_id"]) if o["id"] != id])
        except ValueError:
            raise ValueError("Não dá para trocar o ativo: vendas posteriores do ativo antigo ficariam "
                             "sem cotas suficientes.") from None
    row["asset_id"] = asset_id
    saved = finance._save(conn, "asset_ops", row, id)
    if moved:
        _drop_empty_asset(conn, old["asset_id"])
    return saved


def _drop_empty_asset(conn, asset_id: int) -> None:
    has_ops = conn.execute("SELECT 1 FROM asset_ops WHERE asset_id = ?", (asset_id,)).fetchone()
    has_div = conn.execute("SELECT 1 FROM dividends WHERE asset_id = ?", (asset_id,)).fetchone()
    if not has_ops and not has_div:
        conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))


def delete_op(conn, id: int) -> None:
    row = conn.execute("SELECT * FROM asset_ops WHERE id = ?", (id,)).fetchone()
    if row is None:
        raise ValueError("Operação não encontrada.")
    rest = [o for o in _asset_ops(conn, row["asset_id"]) if o["id"] != id]
    try:
        replay(rest)
    except ValueError:
        raise ValueError("Não dá para excluir: vendas posteriores ficariam sem cotas suficientes. "
                         "Exclua primeiro essas vendas.") from None
    conn.execute("DELETE FROM asset_ops WHERE id = ?", (id,))
    _drop_empty_asset(conn, row["asset_id"])


def save_dividend(conn, d: dict, id: int | None = None) -> int:
    ticker = clean_ticker(d.get("ticker"))
    old = None
    if id is not None:
        old = conn.execute("SELECT asset_id FROM dividends WHERE id = ?", (id,)).fetchone()
        if old is None:
            raise ValueError("Provento não encontrado.")
    asset = conn.execute("SELECT id FROM assets WHERE ticker = ?", (ticker,)).fetchone()
    if asset is None:
        raise ValueError(f"Registre primeiro uma compra de {ticker} antes de lançar proventos.")
    row = {
        "asset_id": asset["id"],
        "date": v.iso_date(d, "date"),
        "amount": round(v.number(d, "amount", "O valor recebido"), 2),
        "tipo": v.choice(d, "tipo", "O tipo de provento", DIVIDEND_TYPES) if d.get("tipo") else "Dividendo",
        "affects_cash": v.boolean(d, "affects_cash", True),
        "account_id": accounts.check_account(conn, d.get("account_id")),
    }
    saved = finance._save(conn, "dividends", row, id)
    if old is not None and old["asset_id"] != asset["id"]:
        _drop_empty_asset(conn, old["asset_id"])
    return saved


def delete_dividend(conn, id: int) -> None:
    row = conn.execute("SELECT asset_id FROM dividends WHERE id = ?", (id,)).fetchone()
    if row is None:
        raise ValueError("Provento não encontrado.")
    conn.execute("DELETE FROM dividends WHERE id = ?", (id,))
    _drop_empty_asset(conn, row["asset_id"])


def set_price(conn, asset_id: int, d: dict) -> None:
    raw = d.get("price")
    price = None if raw in (None, "") else round(v.number(d, "price", "A cotação"), 4)
    if conn.execute("UPDATE assets SET current_price = ? WHERE id = ?", (price, asset_id)).rowcount == 0:
        raise ValueError("Ativo não encontrado.")


def delete_asset(conn, asset_id: int) -> None:
    finance._delete(conn, "assets", asset_id)


def asset_history(conn, asset_id: int) -> dict:
    asset = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if asset is None:
        raise ValueError("Ativo não encontrado.")
    ops = db.rows(conn, "SELECT * FROM asset_ops WHERE asset_id = ? ORDER BY date DESC, id DESC", (asset_id,))
    divs = db.rows(conn, "SELECT * FROM dividends WHERE asset_id = ? ORDER BY date DESC, id DESC", (asset_id,))
    return {"asset": dict(asset), "ops": ops, "dividends": divs}


def positions(conn, today: str) -> list[dict]:
    assets = db.rows(conn, "SELECT * FROM assets ORDER BY ticker")
    ops = _group(db.rows(conn, "SELECT * FROM asset_ops"), "asset_id")
    divs = _group(db.rows(conn, "SELECT * FROM dividends"), "asset_id")
    cutoff = (date.fromisoformat(today) - timedelta(days=365)).isoformat()
    out = []
    for a in assets:
        r = replay(ops.get(a["id"], []))
        received = [d for d in divs.get(a["id"], []) if d["date"] <= today]
        div12 = sum(d["amount"] for d in received if d["date"] > cutoff)
        div_total = sum(d["amount"] for d in received)
        qty, cost, price = r["qty"], r["cost"], a["current_price"]
        value = qty * price if price is not None else cost
        out.append({
            "id": a["id"], "ticker": a["ticker"], "kind": a["kind"], "kind_label": ASSET_KINDS.get(a["kind"], a["kind"]),
            "qty": qty, "avg_price": round(r["avg"], 4), "cost": round(cost, 2),
            "current_price": price, "value": round(value, 2),
            "result": round(value - cost, 2) if price is not None else None,
            "result_pct": round((value / cost - 1) * 100, 2) if price is not None and cost else None,
            "div_12m": round(div12, 2), "div_total": round(div_total, 2),
            "yoc_12m": round(div12 / cost * 100, 2) if cost > 0 else None,
            "yoc_total": round(div_total / cost * 100, 2) if cost > 0 else None,
            "realized": round(r["realized"], 2), "closed": qty == 0,
        })
    return out


# ================================================================ renda fixa (% do CDI)

def monthly_rate(cdi_annual: float, pct_cdi: float) -> float:
    """Taxa mensal equivalente a pct_cdi % do CDI anual."""
    return (1 + cdi_annual / 100 * pct_cdi / 100) ** (1 / 12) - 1


def save_fi(conn, d: dict, id: int | None = None) -> int:
    row = {
        "name": v.text(d, "name", "o nome do investimento", maxlen=60),
        "pct_cdi": v.number(d, "pct_cdi", "O percentual do CDI"),
        "tax": v.choice(d, "tax", "A tributação", TAX_REGIMES) if d.get("tax") else "regressivo",
    }
    if row["pct_cdi"] > 1000:
        raise ValueError("Percentual do CDI muito alto.")
    return finance._save(conn, "fixed_income", row, id)


def delete_fi(conn, id: int) -> None:
    finance._delete(conn, "fixed_income", id)


_MOVE_ORDER = {"aporte": 0, "rendimento": 1, "resgate": 2}


def _check_fi_balance(moves: list[dict]) -> None:
    saldo = 0.0
    for m in sorted(moves, key=lambda m: (m["date"], _MOVE_ORDER[m["kind"]], m.get("id") or 0)):
        saldo += m["amount"] if m["kind"] != "resgate" else -m["amount"]
        if saldo < -0.005:
            raise ValueError("O resgate é maior que o saldo disponível nesse investimento.")


def save_fi_move(conn, d: dict, id: int | None = None) -> int:
    if id is not None:  # na edição o investimento não muda
        old = conn.execute("SELECT fi_id FROM fixed_income_moves WHERE id = ?", (id,)).fetchone()
        if old is None:
            raise ValueError("Movimentação não encontrada.")
        fi_id = old["fi_id"]
    else:
        fi_id = v.integer(d, "fi_id", "O investimento")
        if conn.execute("SELECT 1 FROM fixed_income WHERE id = ?", (fi_id,)).fetchone() is None:
            raise ValueError("Investimento não encontrado.")
    kind = v.choice(d, "kind", "O tipo de movimentação", FI_MOVE_KINDS)
    row = {
        "fi_id": fi_id,
        "date": v.iso_date(d, "date"),
        "kind": kind,
        "amount": round(v.number(d, "amount", "O valor"), 2),
        "affects_cash": 0 if kind == "rendimento" else v.boolean(d, "affects_cash", True),
        "account_id": accounts.check_account(conn, d.get("account_id")),
    }
    existing = [m for m in db.rows(conn, "SELECT * FROM fixed_income_moves WHERE fi_id = ?", (fi_id,))
                if m["id"] != id]
    _check_fi_balance(existing + [dict(row, id=id or 10 ** 12)])
    return finance._save(conn, "fixed_income_moves", row, id)


def delete_fi_move(conn, id: int) -> None:
    row = conn.execute("SELECT * FROM fixed_income_moves WHERE id = ?", (id,)).fetchone()
    if row is None:
        raise ValueError("Movimentação não encontrada.")
    rest = [m for m in db.rows(conn, "SELECT * FROM fixed_income_moves WHERE fi_id = ?", (row["fi_id"],))
            if m["id"] != id]
    try:
        _check_fi_balance(rest)
    except ValueError:
        raise ValueError("Não dá para excluir: resgates posteriores ficariam acima do saldo.") from None
    conn.execute("DELETE FROM fixed_income_moves WHERE id = ?", (id,))


def fi_history(conn, fi_id: int) -> dict:
    fi = conn.execute("SELECT * FROM fixed_income WHERE id = ?", (fi_id,)).fetchone()
    if fi is None:
        raise ValueError("Investimento não encontrado.")
    moves = db.rows(conn, "SELECT * FROM fixed_income_moves WHERE fi_id = ? ORDER BY date DESC, id DESC", (fi_id,))
    return {"fi": dict(fi), "moves": moves}


def _balance(moves: list[dict], cutoff: str | None = None) -> float:
    total = 0.0
    for m in moves:
        if cutoff is None or m["date"] <= cutoff:
            total += -m["amount"] if m["kind"] == "resgate" else m["amount"]
    return total


# ---- imposto estimado (IR regressivo + IOF) -------------------------------------------------
IR_BRACKETS = ((180, 0.225), (360, 0.20), (720, 0.175))  # até N dias; acima de 720: 15%
IR_LAST = 0.15
IOF_BY_DAY = (96, 93, 90, 86, 83, 80, 76, 73, 70, 66, 63, 60, 56, 53, 50, 46, 43, 40, 36, 33, 30, 26, 23,
              20, 16, 13, 10, 6, 3)  # % do rendimento no dia 1..29 (a partir do dia 30 não há IOF)


def ir_rate(days: int) -> float:
    for limit, rate in IR_BRACKETS:
        if days <= limit:
            return rate
    return IR_LAST


def iof_rate(days: int) -> float:
    d = max(days, 1)
    return IOF_BY_DAY[d - 1] / 100 if d <= len(IOF_BY_DAY) else 0.0


def fi_tax(moves: list[dict], ref: str, balance: float, regime: str = "regressivo") -> dict:
    """Imposto estimado se TUDO fosse resgatado em `ref` (AAAA-MM-DD), com saldo bruto `balance`.

    O rendimento acumulado (saldo - principal) é repartido entre os aportes em proporção a
    valor x dias aplicados; cada parte paga o IOF (se < 30 dias) e a alíquota regressiva da sua idade.
    Resgates consomem os aportes mais antigos primeiro. É uma estimativa: o imposto real depende do
    extrato da instituição.
    """
    zero = {"ir": 0.0, "iof": 0.0, "total": 0.0, "yield": 0.0, "avg_rate": 0.0}
    if regime == "isento":
        return zero
    order = {"aporte": 0, "rendimento": 1, "resgate": 2}
    lots: list[list] = []  # [data, principal]
    bal = 0.0
    for m in sorted((m for m in moves if m["date"] <= ref), key=lambda m: (m["date"], order[m["kind"]], m.get("id") or 0)):
        if m["kind"] == "aporte":
            lots.append([m["date"], m["amount"]])
            bal += m["amount"]
        elif m["kind"] == "rendimento":
            bal += m["amount"]
        else:
            principal = sum(l[1] for l in lots)
            take = m["amount"] * principal / bal if bal > 0 else 0.0
            for lot in lots:
                used = min(lot[1], take)
                lot[1] -= used
                take -= used
            bal -= m["amount"]
    principal = sum(l[1] for l in lots)
    gain = max(0.0, balance - principal)
    if gain <= 0.005 or principal <= 0:
        return zero
    end = date.fromisoformat(ref)
    aged = [(max((end - date.fromisoformat(l[0])).days, 0), l[1]) for l in lots if l[1] > 0]
    weights = [amount * max(days, 1) for days, amount in aged]
    total_w = sum(weights) or 1.0
    ir = iof = 0.0
    for (days, _), w in zip(aged, weights):
        share = gain * w / total_w
        part_iof = share * iof_rate(days)
        iof += part_iof
        ir += (share - part_iof) * ir_rate(days)
    return {"ir": round(ir, 2), "iof": round(iof, 2), "total": round(ir + iof, 2), "yield": round(gain, 2),
            "avg_rate": round((ir + iof) / gain * 100, 2)}


def fi_products(conn, today: str, cdi: float) -> list[dict]:
    products = db.rows(conn, "SELECT * FROM fixed_income ORDER BY name")
    moves = _group(db.rows(conn, "SELECT * FROM fixed_income_moves"), "fi_id")
    year, month = int(today[:4]), int(today[5:7])
    out = []
    for p in products:
        ms = moves.get(p["id"], [])
        saldo = _balance(ms)
        m_rate = monthly_rate(cdi, p["pct_cdi"])
        left = 12 - month
        dec_balance = saldo * (1 + m_rate) ** left
        tax_now = fi_tax(ms, today, _balance(ms, today), p["tax"])
        tax_dec = fi_tax(ms, f"{year}-12-31", dec_balance, p["tax"])
        out.append({
            "id": p["id"], "name": p["name"], "pct_cdi": p["pct_cdi"], "tax": p["tax"],
            "tax_label": TAX_REGIMES.get(p["tax"], p["tax"]),
            "ir_hoje": tax_now["total"], "aliquota_hoje": tax_now["avg_rate"],
            "liquido_hoje": round(saldo - tax_now["total"], 2),
            "ir_dezembro": tax_dec["total"], "liquido_dezembro": round(dec_balance - tax_dec["total"], 2),
            "saldo": round(saldo, 2),
            "aportado": round(sum(m["amount"] for m in ms if m["kind"] == "aporte")
                              - sum(m["amount"] for m in ms if m["kind"] == "resgate"), 2),
            "rendimento_total": round(sum(m["amount"] for m in ms if m["kind"] == "rendimento"), 2),
            "rendimento_ano": round(sum(m["amount"] for m in ms
                                        if m["kind"] == "rendimento" and m["date"][:4] == str(year)), 2),
            "taxa_anual": round(cdi * p["pct_cdi"] / 100, 2),
            "taxa_mensal": round(m_rate * 100, 4),
            "projecao_resto": round(saldo * ((1 + m_rate) ** left - 1), 2),
            "saldo_dezembro": round(saldo * (1 + m_rate) ** left, 2),
        })
    return out


# ================================================================ séries e projeção

def series(conn, months: list[str]) -> list[dict]:
    """Patrimônio investido ao fim de cada mês (renda variável a preço de custo)."""
    ops = _group(db.rows(conn, "SELECT * FROM asset_ops"), "asset_id")
    moves = db.rows(conn, "SELECT * FROM fixed_income_moves")
    out = []
    for ym in months:
        cutoff = finance.month_day(ym, 31)
        rv = sum(replay([o for o in group if o["date"] <= cutoff])["cost"] for group in ops.values())
        rf = _balance(moves, cutoff)
        out.append({"ym": ym, "rf": round(rf, 2), "rv": round(rv, 2), "total": round(rf + rv, 2)})
    return out


def projection(conn, today: str, cdi: float) -> dict:
    """Mês a mês do ano corrente: realizado até o mês atual, projetado de lá até dezembro.

    - Renda fixa: cada investimento rende (% do CDI) ao mês sobre o saldo do fim do mês anterior.
    - Proventos: média mensal recebida desde o primeiro provento dos últimos 12 meses (máx. 12 meses).
    - Renda variável: mantida a preço de custo (não se projeta valorização das cotas).
    """
    year, cur = int(today[:4]), int(today[5:7])
    cur_end = finance.month_day(f"{year}-{cur:02d}", 31)
    products = db.rows(conn, "SELECT * FROM fixed_income")
    moves = _group(db.rows(conn, "SELECT * FROM fixed_income_moves"), "fi_id")
    dividends = [d for d in db.rows(conn, "SELECT * FROM dividends") if d["date"] <= today]
    ops = _group(db.rows(conn, "SELECT * FROM asset_ops"), "asset_id")

    cutoff12 = (date.fromisoformat(today) - timedelta(days=365)).isoformat()
    recent = [d for d in dividends if d["date"] > cutoff12]
    if recent:
        span = min(12, finance.ym_diff(min(d["date"] for d in recent)[:7], today[:7]) + 1)
        avg_div = sum(d["amount"] for d in recent) / max(span, 1)
    else:
        avg_div = 0.0

    rv_cost = sum(replay([o for o in g if o["date"] <= cur_end])["cost"] for g in ops.values())
    balances = {p["id"]: _balance(moves.get(p["id"], []), cur_end) for p in products}
    rates = {p["id"]: monthly_rate(cdi, p["pct_cdi"]) for p in products}

    rows = []
    for m in range(1, 13):
        ym = f"{year}-{m:02d}"
        end = finance.month_day(ym, 31)
        if m <= cur:
            rf = sum(_balance(moves.get(p["id"], []), end) for p in products)
            rf_yield = sum(x["amount"] for p in products for x in moves.get(p["id"], [])
                           if x["kind"] == "rendimento" and x["date"][:7] == ym)
            div = sum(d["amount"] for d in dividends if d["date"][:7] == ym)
            rv = sum(replay([o for o in g if o["date"] <= end])["cost"] for g in ops.values())
            projected = False
        else:
            rf_yield = sum(balances[p["id"]] * rates[p["id"]] for p in products)
            for p in products:
                balances[p["id"]] *= 1 + rates[p["id"]]
            rf = sum(balances.values())
            div, rv, projected = avg_div, rv_cost, True
        rows.append({"ym": ym, "rf": round(rf, 2), "rv": round(rv, 2), "total": round(rf + rv, 2),
                     "rf_yield": round(rf_yield, 2), "div": round(div, 2), "projected": projected})

    ir_dec = ir_start = 0.0
    for p in products:
        ms = moves.get(p["id"], [])
        ir_dec += fi_tax(ms, f"{year}-12-31", balances[p["id"]], p["tax"])["total"]
        ir_start += fi_tax(ms, f"{year - 1}-12-31", _balance(ms, f"{year - 1}-12-31"), p["tax"])["total"]
    ir_year = max(0.0, ir_dec - ir_start)

    done = [r for r in rows if not r["projected"]]
    todo = [r for r in rows if r["projected"]]
    ytd_rf = sum(r["rf_yield"] for r in done)
    ytd_div = sum(r["div"] for r in done)
    proj_rf = sum(r["rf_yield"] for r in todo)
    proj_div = sum(r["div"] for r in todo)
    return {
        "year": year, "current_month": cur, "cdi_annual": cdi, "avg_monthly_dividends": round(avg_div, 2),
        "rv_cost": round(rv_cost, 2),
        "rows": rows,
        "summary": {
            "ytd_rf": round(ytd_rf, 2), "ytd_div": round(ytd_div, 2),
            "proj_rf": round(proj_rf, 2), "proj_div": round(proj_div, 2),
            "ytd_total": round(ytd_rf + ytd_div, 2),
            "year_total": round(ytd_rf + ytd_div + proj_rf + proj_div, 2),
            "yoc_year": round((ytd_div + proj_div) / rv_cost * 100, 2) if rv_cost > 0 else None,
            "patrimonio_dezembro": rows[-1]["total"],
            "ir_dezembro": round(ir_dec, 2), "ir_ano": round(ir_year, 2),
            "year_total_liquido": round(ytd_rf + ytd_div + proj_rf + proj_div - ir_year, 2),
            "patrimonio_dezembro_liquido": round(rows[-1]["total"] - ir_dec, 2),
        },
    }
