"""Controle financeiro mensal: lançamentos, despesas fixas, parcelamentos, cartão e saldo com transporte."""
from __future__ import annotations

from . import accounts, cards, db
from . import validate as v
from .common import amount_at, month_day, ym_add, ym_diff  # noqa: F401  (reexportados)

KINDS = ("entrada", "saida")
PAY_SOURCES = ("lancamento", "fixa", "parcela", "fatura")
LINKABLE = ("lancamento", "fixa", "parcela")  # o que pode ser quitado antes do prazo por outro lançamento
WARN_AT = 80  # % do orçamento a partir do qual a categoria entra em alerta


# ---------------------------------------------------------------- CRUD genérico

def _save(conn, table: str, row: dict, id: int | None = None) -> int:
    cols = list(row)
    values = [row[c] for c in cols]
    if id is None:
        cur = conn.execute(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", values)
        return cur.lastrowid
    cur = conn.execute(f"UPDATE {table} SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?", values + [id])
    if cur.rowcount == 0:
        raise ValueError("Registro não encontrado.")
    return id


def _delete(conn, table: str, id: int) -> None:
    if conn.execute(f"DELETE FROM {table} WHERE id = ?", (id,)).rowcount == 0:
        raise ValueError("Registro não encontrado.")


def _clear_item(conn, source: str, ref_type: str | None, id: int) -> None:
    conn.execute("DELETE FROM payment_marks WHERE source = ? AND ref_id = ?", (source, id))
    conn.execute("DELETE FROM occurrence_links WHERE source = ? AND ref_id = ?", (source, id))
    if ref_type:
        conn.execute("DELETE FROM amount_changes WHERE ref_type = ? AND ref_id = ?", (ref_type, id))


# ---------------------------------------------------------------- lançamentos avulsos

def save_transaction(conn, d: dict, id: int | None = None) -> int:
    kind = v.choice(d, "kind", "O tipo", KINDS)
    row = {
        "date": v.iso_date(d, "date"),
        "kind": kind,
        "description": v.text(d, "description", "a descrição"),
        "category": v.text(d, "category", "a categoria", required=False, maxlen=40),
        "amount": round(v.number(d, "amount", "O valor"), 2),
        "card_id": cards.check_card(conn, d.get("card_id")) if kind == "saida" else None,
        "account_id": accounts.check_account(conn, d.get("account_id")),
    }
    repeat = 0
    if id is None and d.get("repeat") not in (None, "", 0, "0"):
        repeat = v.integer(d, "repeat", "A repetição", 0, 60)
    first = _save(conn, "transactions", row, id)
    if "settles" in d:  # quita um débito (ou recebimento) previsto; None desfaz o vínculo
        set_settlement(conn, first, d.get("settles"))
    elif id is not None:
        _revalidate_settlement(conn, first)
    day = int(row["date"][8:10])
    for k in range(1, repeat + 1):  # cópias nos meses seguintes, mesmo dia (ajustado em meses curtos)
        _save(conn, "transactions", dict(row, date=month_day(ym_add(row["date"][:7], k), day)))
    return first


def delete_transaction(conn, id: int) -> None:
    _delete(conn, "transactions", id)
    _clear_item(conn, "lancamento", None, id)


def suggestions(conn) -> list[dict]:
    """Últimos lançamentos distintos, para preencher rápido categoria e valor."""
    return db.rows(conn, """
        SELECT kind, description, category, amount, card_id, account_id FROM transactions
        WHERE id IN (SELECT MAX(id) FROM transactions GROUP BY kind, LOWER(description))
        ORDER BY id DESC LIMIT 300""")


# ---------------------------------------------------------------- reajustes de valor

def _apply_amount(conn, ref_type: str, table: str, col: str, id: int, start: str, d: dict, amount_key: str,
                  label: str, creating: bool) -> dict:
    """Valor do item na gravação.

    Na criação vale o valor informado. Na edição o valor só muda se vier `amount_from` (mês de
    aplicação): a partir dele o novo valor passa a valer e os meses anteriores ficam como estavam.
    """
    if creating:
        return {col: round(v.number(d, amount_key, label), 2)}
    old = conn.execute(f"SELECT {col} FROM {table} WHERE id = ?", (id,)).fetchone()
    if old is None:
        raise ValueError("Registro não encontrado.")
    amount_from = v.month(d, "amount_from", "o mês de aplicação do novo valor", required=False)
    if not amount_from or d.get(amount_key) in (None, ""):
        return {col: old[0]}
    amount = round(v.number(d, amount_key, label), 2)
    if amount_from <= start:
        return {col: amount}
    changes = db.rows(conn, "SELECT * FROM amount_changes WHERE ref_type = ? AND ref_id = ?", (ref_type, id))
    if abs(amount_at(old[0], changes, amount_from) - amount) >= 0.005:
        conn.execute(
            "INSERT INTO amount_changes(ref_type, ref_id, from_month, amount) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(ref_type, ref_id, from_month) DO UPDATE SET amount = excluded.amount",
            (ref_type, id, amount_from, amount))
    return {col: old[0]}


def delete_amount_change(conn, id: int) -> None:
    _delete(conn, "amount_changes", id)


def _changes_by_ref(conn) -> dict:
    out: dict = {}
    for c in db.rows(conn, "SELECT * FROM amount_changes ORDER BY from_month"):
        out.setdefault((c["ref_type"], c["ref_id"]), []).append(c)
    return out


# ---------------------------------------------------------------- despesas/receitas fixas

def save_fixed(conn, d: dict, id: int | None = None) -> int:
    row = {
        "kind": v.choice(d, "kind", "O tipo", KINDS),
        "description": v.text(d, "description", "a descrição"),
        "category": v.text(d, "category", "a categoria", required=False, maxlen=40),
        "day": v.integer(d, "day", "O dia do mês", 1, 31),
        "start_month": v.month(d, "start_month", "o mês de início"),
        "end_month": v.month(d, "end_month", required=False),
        "account_id": accounts.check_account(conn, d.get("account_id")),
    }
    if row["end_month"] and row["end_month"] < row["start_month"]:
        raise ValueError("O mês final não pode ser anterior ao mês de início.")
    row.update(_apply_amount(conn, "fixed", "fixed_items", "amount", id, row["start_month"], d, "amount",
                             "O valor", id is None))
    return _save(conn, "fixed_items", row, id)


def delete_fixed(conn, id: int) -> None:
    _delete(conn, "fixed_items", id)
    _clear_item(conn, "fixa", "fixed", id)


def list_fixed(conn, today_ym: str) -> list[dict]:
    out = db.rows(conn, "SELECT * FROM fixed_items ORDER BY kind DESC, description")
    changes = _changes_by_ref(conn)
    for f in out:
        f["changes"] = changes.get(("fixed", f["id"]), [])
        f["base_amount"] = f["amount"]
        ref = min(today_ym, f["end_month"]) if f["end_month"] else today_ym
        f["amount"] = amount_at(f["base_amount"], f["changes"], max(ref, f["start_month"]))
        if f["start_month"] > today_ym:
            f["status"] = "futura"
        elif f["end_month"] and f["end_month"] < today_ym:
            f["status"] = "encerrada"
        else:
            f["status"] = "ativa"
    return out


# ---------------------------------------------------------------- compras parceladas

def save_installment(conn, d: dict, id: int | None = None) -> int:
    row = {
        "description": v.text(d, "description", "a descrição"),
        "category": v.text(d, "category", "a categoria", required=False, maxlen=40),
        "first_date": v.iso_date(d, "first_date", "data da primeira parcela"),
        "n_installments": v.integer(d, "n_installments", "O número de parcelas", 1, 600),
        "card_id": cards.check_card(conn, d.get("card_id")),
        "account_id": accounts.check_account(conn, d.get("account_id")),
    }
    row.update(_apply_amount(conn, "installment", "installments", "installment_amount", id, row["first_date"][:7],
                             d, "installment_amount", "O valor da parcela", id is None))
    return _save(conn, "installments", row, id)


def delete_installment(conn, id: int) -> None:
    _delete(conn, "installments", id)
    _clear_item(conn, "parcela", "installment", id)


def list_installments(conn, today_ym: str) -> list[dict]:
    out = db.rows(conn, "SELECT * FROM installments ORDER BY first_date, description")
    changes = _changes_by_ref(conn)
    names = {c["id"]: c["name"] for c in db.rows(conn, "SELECT id, name FROM credit_cards")}
    for i in out:
        n = i["n_installments"]
        first = i["first_date"][:7]
        i["changes"] = changes.get(("installment", i["id"]), [])
        i["base_amount"] = i["installment_amount"]
        values = [amount_at(i["base_amount"], i["changes"], ym_add(first, k)) for k in range(n)]
        paid = max(0, min(n, ym_diff(first, today_ym) + 1))  # parcelas até o mês atual
        i["paid"] = paid
        i["remaining"] = n - paid
        i["installment_amount"] = values[min(max(paid - 1, 0), n - 1)] if paid else values[0]
        i["total"] = round(sum(values), 2)
        i["remaining_amount"] = round(sum(values[paid:]), 2)
        i["last_month"] = ym_add(first, n - 1)
        i["status"] = "futura" if paid == 0 else ("quitada" if paid >= n else "em andamento")
        i["card_name"] = names.get(i["card_id"])
    return out


# ---------------------------------------------------------------- pago / a pagar

def _default_date(conn, source: str, ref_id: int, ym: str) -> str:
    """Data de vencimento da saída; sem marca manual, ela conta como paga quando essa data chega."""
    if source == "lancamento":
        row = conn.execute("SELECT date, kind, card_id FROM transactions WHERE id = ?", (ref_id,)).fetchone()
        if row is None or row["kind"] != "saida":
            raise ValueError("Saída não encontrada.")
        if row["card_id"]:
            raise ValueError("Compra no cartão: marque como paga a fatura do cartão.")
        return row["date"]
    if source == "fixa":
        row = conn.execute("SELECT day, kind FROM fixed_items WHERE id = ?", (ref_id,)).fetchone()
        if row is None or row["kind"] != "saida":
            raise ValueError("Despesa fixa não encontrada.")
        return month_day(ym, row["day"])
    if source == "parcela":
        row = conn.execute("SELECT first_date, card_id FROM installments WHERE id = ?", (ref_id,)).fetchone()
        if row is None:
            raise ValueError("Compra parcelada não encontrada.")
        if row["card_id"]:
            raise ValueError("Compra no cartão: marque como paga a fatura do cartão.")
        return month_day(ym, int(row["first_date"][8:10]))
    row = conn.execute("SELECT due_day FROM credit_cards WHERE id = ?", (ref_id,)).fetchone()
    if row is None:
        raise ValueError("Cartão não encontrado.")
    return month_day(ym, row["due_day"])


def set_payment(conn, d: dict, today: str) -> dict:
    source = v.choice(d, "source", "A origem", PAY_SOURCES)
    ref_id = v.integer(d, "ref_id", "O item")
    ym = v.month(d, "ym", "o mês")
    paid = v.boolean(d, "paid", True)
    if conn.execute("SELECT 1 FROM occurrence_links WHERE source = ? AND ref_id = ? AND ym = ?",
                    (source, ref_id, ym)).fetchone():
        raise ValueError("Esse item já foi quitado por um lançamento. Desfaça o vínculo para mudar.")
    if source == "fatura":
        data = load_all(conn)
        card = next((c for c in data["cards"] if c["id"] == ref_id), None)
        st = cards.statement_item(data, card, ym) if card else None
        if st and st.get("prepaid_total") and st["amount"] <= 0.004:
            raise ValueError("Esta fatura já foi quitada pelos pagamentos registrados. Exclua um pagamento para mudar.")
    default = 1 if _default_date(conn, source, ref_id, ym) <= today else 0
    if paid == default:  # volta ao automático
        conn.execute("DELETE FROM payment_marks WHERE source = ? AND ref_id = ? AND ym = ?", (source, ref_id, ym))
    else:
        conn.execute(
            "INSERT INTO payment_marks(source, ref_id, ym, paid) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(source, ref_id, ym) DO UPDATE SET paid = excluded.paid", (source, ref_id, ym, paid))
    return {"paid": bool(paid)}


# ---------------------------------------------------------------- quitar débito previsto com um lançamento

def _link_target(d: dict) -> tuple[str, int, str]:
    return (v.choice(d, "source", "A origem", LINKABLE), v.integer(d, "ref_id", "O item"), v.month(d, "ym", "o mês"))


def _find_item(data: dict, source: str, ref_id: int, ym: str, today: str) -> dict | None:
    return next((i for i in items_for_month(data, ym, today)
                 if i["source"] == source and i["ref_id"] == ref_id and not i.get("transfer")), None)


def set_settlement(conn, tx_id: int, target) -> None:
    """Diz que o lançamento `tx_id` já quitou o débito (ou recebimento) previsto `target`.

    O previsto continua na lista do mês dele, marcado como quitado, mas com valor zero: o dinheiro
    movimenta o saldo uma vez só, na data do lançamento. `target` vazio desfaz o vínculo."""
    conn.execute("DELETE FROM occurrence_links WHERE tx_id = ?", (tx_id,))
    if not target:
        return
    if not isinstance(target, dict):
        raise ValueError("Débito previsto inválido.")
    source, ref_id, ym = _link_target(target)
    data = load_all(conn)
    tx = next((t for t in data["transactions"] if t["id"] == tx_id), None)
    if tx is None:
        raise ValueError("Lançamento não encontrado.")
    if tx.get("card_id"):
        raise ValueError("Compra no cartão não quita débito direto da conta. Marque a fatura como paga.")
    if source == "lancamento" and ref_id == tx_id:
        raise ValueError("Um lançamento não pode quitar a si mesmo.")
    item = _find_item(data, source, ref_id, ym, "9999-12-31")
    if item is None:
        raise ValueError("Esse débito não existe nesse mês.")
    if item.get("linked"):
        raise ValueError("Esse débito já foi quitado por outro lançamento.")
    if item["kind"] != tx["kind"]:
        raise ValueError("O lançamento e o débito precisam ser do mesmo tipo (os dois entrada ou os dois saída).")
    if item["account_id"] != tx["account_id"]:
        raise ValueError("O lançamento e o débito estão em contas diferentes.")
    conn.execute("DELETE FROM payment_marks WHERE source = ? AND ref_id = ? AND ym = ?", (source, ref_id, ym))
    conn.execute("INSERT INTO occurrence_links(source, ref_id, ym, tx_id) VALUES (?, ?, ?, ?)",
                 (source, ref_id, ym, tx_id))


def _revalidate_settlement(conn, tx_id: int) -> None:
    """Depois de editar um lançamento, desfaz o vínculo se ele deixou de fazer sentido (tipo, conta, cartão)."""
    row = conn.execute("SELECT source, ref_id, ym FROM occurrence_links WHERE tx_id = ?", (tx_id,)).fetchone()
    if row is None:
        return
    try:
        set_settlement(conn, tx_id, dict(row))
    except ValueError:
        conn.execute("DELETE FROM occurrence_links WHERE tx_id = ?", (tx_id,))


def add_link(conn, d: dict) -> dict:
    set_settlement(conn, v.integer(d, "tx_id", "O lançamento"), d)
    return {"ok": True}


def remove_link(conn, id: int) -> None:
    _delete(conn, "occurrence_links", id)


def link_options(conn, kind: str, today: str, tx_id: int | None = None) -> list[dict]:
    """Débitos (ou recebimentos) previstos que um lançamento deste tipo pode quitar: os ainda a pagar/receber
    ou marcados como pagos à mão, do mês anterior a daqui a 3 meses. O que `tx_id` já quita aparece como 'current'."""
    if kind not in KINDS:
        raise ValueError("O tipo é inválido.")
    data = load_all(conn)
    mine = next((l for l in data["links"].values() if tx_id and l["tx_id"] == tx_id), None)
    out = []
    for k in range(5):
        ym = ym_add(ym_add(today[:7], -1), k)
        for i in items_for_month(data, ym, today):
            if i["source"] not in LINKABLE or i.get("transfer") or i["kind"] != kind:
                continue
            if i["source"] == "lancamento" and i["ref_id"] == tx_id:
                continue
            current = bool(mine and (mine["source"], mine["ref_id"], mine["ym"]) == (i["source"], i["ref_id"], ym))
            if i.get("linked") and not current:
                continue
            if not (current or i["pending"] or i.get("manual")):
                continue
            out.append({"source": i["source"], "ref_id": i["ref_id"], "ym": ym, "date": i["date"],
                        "description": i["description"], "category": i["category"],
                        "amount": i.get("original_amount", i["amount"]), "account_id": i["account_id"],
                        "paid": bool(i.get("paid")) and not i.get("linked"), "current": current})
    out.sort(key=lambda o: (o["date"], o["description"].lower()))
    return out


def link_candidates(conn, d: dict, today: str) -> dict:
    """Lançamentos já feitos que poderiam ter quitado o débito `d` (mesmo tipo e conta, sem cartão, ainda sem vínculo),
    com os de mesmo valor primeiro. Janela: de 90 dias antes a 31 dias depois do vencimento."""
    from datetime import date as _date
    source, ref_id, ym = _link_target(d)
    data = load_all(conn)
    item = _find_item(data, source, ref_id, ym, today)
    if item is None:
        raise ValueError("Esse débito não existe nesse mês.")
    if item.get("linked"):
        raise ValueError("Esse débito já foi quitado por um lançamento.")
    amount = item["amount"]
    used = {l["tx_id"] for l in data["links"].values()}
    due = _date.fromisoformat(item["date"])
    cands = []
    for t in data["transactions"]:
        if t["kind"] != item["kind"] or t.get("card_id") or t["id"] in used or t["account_id"] != item["account_id"]:
            continue
        if source == "lancamento" and t["id"] == ref_id:
            continue
        gap = (_date.fromisoformat(t["date"]) - due).days
        if gap < -90 or gap > 31:
            continue
        cands.append({"id": t["id"], "date": t["date"], "description": t["description"], "category": t["category"],
                      "amount": t["amount"], "same_amount": abs(t["amount"] - amount) < 0.005, "gap": abs(gap)})
    cands.sort(key=lambda c: (not c["same_amount"], c["gap"], c["date"]))
    return {"item": {"source": source, "ref_id": ref_id, "ym": ym, "description": item["description"],
                     "date": item["date"], "amount": amount, "kind": item["kind"]},
            "candidates": cands[:40]}


# ---------------------------------------------------------------- orçamento por categoria

def list_budgets(conn) -> list[dict]:
    return db.rows(conn, "SELECT * FROM budgets ORDER BY category COLLATE NOCASE")


def save_budget(conn, d: dict, id: int | None = None) -> int:
    row = {"category": v.text(d, "category", "a categoria", maxlen=40),
           "amount": round(v.number(d, "amount", "O limite mensal"), 2)}
    if id is None:
        same = conn.execute("SELECT id FROM budgets WHERE category = ?", (row["category"],)).fetchone()
        id = same["id"] if same else None
    return _save(conn, "budgets", row, id)


def delete_budget(conn, id: int) -> None:
    _delete(conn, "budgets", id)


def budget_status(budgets: list[dict], by_category: list[dict]) -> list[dict]:
    spent: dict[str, float] = {}
    for c in by_category:  # "Mercado" e "mercado" contam juntos
        key = c["category"].strip().lower()
        spent[key] = spent.get(key, 0.0) + c["total"]
    out = []
    for b in budgets:
        total = round(spent.get(b["category"].strip().lower(), 0.0), 2)
        pct = round(total / b["amount"] * 100, 1)
        out.append({"id": b["id"], "category": b["category"], "limit": b["amount"], "spent": total, "pct": pct,
                    "left": round(b["amount"] - total, 2),
                    "state": "over" if pct > 100 else ("warn" if pct >= WARN_AT else "ok")})
    out.sort(key=lambda b: -b["pct"])
    return out


def categories(conn) -> list[str]:
    rows = conn.execute(
        "SELECT category FROM transactions UNION SELECT category FROM fixed_items "
        "UNION SELECT category FROM installments UNION SELECT category FROM budgets").fetchall()
    return sorted({r[0] for r in rows if r[0]}, key=str.lower)


# ---------------------------------------------------------------- cálculo mensal

def load_all(conn) -> dict:
    changes = _changes_by_ref(conn)
    fixed = db.rows(conn, "SELECT * FROM fixed_items")
    installments = db.rows(conn, "SELECT * FROM installments")
    for f in fixed:
        f["changes"] = changes.get(("fixed", f["id"]), [])
    for i in installments:
        i["changes"] = changes.get(("installment", i["id"]), [])
    default = accounts.default_id(conn)
    own = lambda rows: [{**r, "account_id": r.get("account_id") or default} for r in rows]
    cards_ = [{**c, "pay_account_id": c.get("pay_account_id") or default} for c in db.rows(conn, "SELECT * FROM credit_cards ORDER BY name")]
    return {
        "settings": db.get_settings(conn),
        "accounts": db.rows(conn, "SELECT * FROM accounts ORDER BY id"),
        "default_account": default,
        "transfers": db.rows(conn, "SELECT * FROM transfers"),
        "transactions": own(db.rows(conn, "SELECT * FROM transactions")),
        "fixed": own(fixed),
        "installments": own(installments),
        "cards": cards_,
        "marks": {(m["source"], m["ref_id"], m["ym"]): m["paid"] for m in db.rows(conn, "SELECT * FROM payment_marks")},
        "links": {(l["source"], l["ref_id"], l["ym"]): l for l in db.rows(conn, "SELECT * FROM occurrence_links")},
        "inv_pays": own(db.rows(conn, "SELECT * FROM invoice_payments")),
        "budgets": list_budgets(conn),
        "recon": {(r["source"], r["ref_id"], r["ym"]) for r in db.rows(conn, "SELECT source, ref_id, ym FROM bank_links")},
        "ops": own(db.rows(conn, "SELECT o.*, a.ticker FROM asset_ops o JOIN assets a ON a.id = o.asset_id")),
        "dividends": own(db.rows(conn, "SELECT d.*, a.ticker FROM dividends d JOIN assets a ON a.id = d.asset_id")),
        "fi_moves": own(db.rows(
            conn, "SELECT m.*, f.name FROM fixed_income_moves m JOIN fixed_income f ON f.id = m.fi_id")),
    }


def _item(source, ref_id, date, description, category, kind, amount, tag="", **extra):
    return {"source": source, "ref_id": ref_id, "date": date, "description": description,
            "category": category, "kind": kind, "amount": round(amount, 2), "tag": tag, **extra}


def items_for_month(data: dict, ym: str, today: str, account: int | None = None) -> list[dict]:
    """Tudo que movimenta o caixa no mês.

    kind = 'entrada' | 'saida' | 'invest'. Em 'invest' o valor é assinado:
    positivo = dinheiro que saiu para investir; negativo = dinheiro que voltou (venda/resgate).

    Ainda não compõem o saldo ('pending'): entrada com data posterior a hoje (a receber) e saída
    não paga (a pagar). Saída sem marca manual conta como paga quando a data de vencimento chega.
    Compras no cartão não aparecem uma a uma: entram numa única saída, a fatura, no vencimento.

    `account` filtra uma conta. Transferências entre contas não alteram o total: no total geral aparecem
    como uma linha neutra (kind 'transfer'); ao filtrar uma conta viram saída/entrada daquela conta.
    """
    items = []
    for t in data["transactions"]:
        if t["date"][:7] == ym and not t.get("card_id"):
            items.append(_item("lancamento", t["id"], t["date"], t["description"], t["category"],
                               t["kind"], t["amount"], account_id=t["account_id"]))
    for f in data["fixed"]:
        if f["start_month"] <= ym and (not f["end_month"] or ym <= f["end_month"]):
            items.append(_item("fixa", f["id"], month_day(ym, f["day"]), f["description"], f["category"],
                               f["kind"], amount_at(f["amount"], f["changes"], ym), "Fixa", account_id=f["account_id"]))
    for i in data["installments"]:
        idx = ym_diff(i["first_date"][:7], ym)
        if 0 <= idx < i["n_installments"] and not i.get("card_id"):
            items.append(_item("parcela", i["id"], month_day(ym, int(i["first_date"][8:10])), i["description"],
                               i["category"], "saida", amount_at(i["installment_amount"], i["changes"], ym),
                               f"{idx + 1}/{i['n_installments']}", account_id=i["account_id"]))
    for card in data["cards"]:
        st = cards.statement_item(data, card, ym)
        if st:
            st["account_id"] = card["pay_account_id"]
            items.append(st)
    card_names = {c["id"]: c["name"] for c in data["cards"]}
    for p in data.get("inv_pays", []):
        if p["date"][:7] == ym:
            items.append(_item("pag_fatura", p["id"], p["date"], f"Pagamento da fatura {card_names.get(p['card_id'], '')}".strip(),
                               "Cartão de crédito", "saida", p["amount"], "Pgto. de fatura", account_id=p["account_id"],
                               card_id=p["card_id"], invoice_ym=p["ym"], note=p["note"], invoice_payment=True))
    for o in data["ops"]:
        if o["affects_cash"] and o["date"][:7] == ym:
            gross = o["quantity"] * o["price"]
            if o["op"] == "compra":
                items.append(_item("invest", o["id"], o["date"], f"Compra {o['ticker']}", "Investimentos",
                                   "invest", gross + o["fees"], "Investimento", account_id=o["account_id"]))
            else:
                items.append(_item("invest", o["id"], o["date"], f"Venda {o['ticker']}", "Investimentos",
                                   "invest", -(gross - o["fees"]), "Investimento", account_id=o["account_id"]))
    for d in data["dividends"]:
        if d["affects_cash"] and d["date"][:7] == ym:
            items.append(_item("dividendo", d["id"], d["date"], f"{d['tipo']} {d['ticker']}", "Investimentos",
                               "entrada", d["amount"], "Provento", account_id=d["account_id"]))
    for m in data["fi_moves"]:
        if m["kind"] != "rendimento" and m["affects_cash"] and m["date"][:7] == ym:
            sign = 1 if m["kind"] == "aporte" else -1
            label = "Aporte" if m["kind"] == "aporte" else "Resgate"
            items.append(_item("invest", m["id"], m["date"], f"{label} · {m['name']}", "Investimentos",
                               "invest", sign * m["amount"], "Investimento", account_id=m["account_id"]))
    names = {a["id"]: a["name"] for a in data["accounts"]}
    for t in data["transfers"]:
        if t["date"][:7] != ym:
            continue
        label = (t["description"] or "Transferência").strip()
        if account is None:
            items.append(_item("transferencia", t["id"], t["date"], f"{label}: {names.get(t['from_account'], '?')} → {names.get(t['to_account'], '?')}",
                               "", "transfer", t["amount"], "Transferência", transfer=True, account_id=t["from_account"],
                               to_account=t["to_account"], from_account=t["from_account"], note=t["description"] or ""))
        elif account == t["from_account"]:
            items.append(_item("transf_saida", t["id"], t["date"], f"{label} → {names.get(t['to_account'], '?')}", "", "saida",
                               t["amount"], "Transferência", transfer=True, account_id=account,
                               from_account=t["from_account"], to_account=t["to_account"], note=t["description"] or ""))
        elif account == t["to_account"]:
            items.append(_item("transf_entrada", t["id"], t["date"], f"{label} ← {names.get(t['from_account'], '?')}", "", "entrada",
                               t["amount"], "Transferência", transfer=True, account_id=account,
                               from_account=t["from_account"], to_account=t["to_account"], note=t["description"] or ""))
    if account is not None:
        items = [i for i in items if i["account_id"] == account or i.get("source") in ("transf_saida", "transf_entrada")]
    marks = data.get("marks", {})
    recon = data.get("recon", set())
    links = data.get("links", {})
    settlers = {l["tx_id"]: l for l in links.values()}
    txs = {t["id"]: t for t in data["transactions"]} if links else {}
    for i in items:
        if i.get("transfer"):
            i["reconciled"] = (i["source"], i["ref_id"], ym) in recon
            i["paid"], i["pending"], i["manual"] = True, False, False
            continue
        i["reconciled"] = (i["source"], i["ref_id"], ym) in recon
        link = links.get((i["source"], i["ref_id"], ym))
        if link:  # já quitado antes do prazo por outro lançamento: aparece, mas não conta de novo
            tx = txs.get(link["tx_id"]) or {}
            i["original_amount"], i["amount"] = i["amount"], 0.0
            i["paid"], i["pending"], i["manual"] = True, False, False
            i["linked"] = {"id": link["id"], "tx_id": link["tx_id"], "date": tx.get("date"),
                           "amount": tx.get("amount"), "description": tx.get("description", "")}
            continue
        if i["source"] == "fatura" and i.get("prepaid_total") and i["amount"] <= 0.004:  # quitada só com pagamentos antecipados
            i["paid"], i["pending"], i["manual"], i["fully_prepaid"] = True, False, False, True
            continue
        if i["source"] == "lancamento" and i["ref_id"] in settlers:
            s_ = settlers[i["ref_id"]]
            i["settles"] = {"link_id": s_["id"], "source": s_["source"], "ref_id": s_["ref_id"], "ym": s_["ym"]}
        if i["kind"] == "saida":
            mark = marks.get((i["source"], i["ref_id"], ym))
            i["paid"] = bool(mark) if mark is not None else i["date"] <= today
            i["pending"] = not i["paid"]
            i["manual"] = mark is not None
        else:
            i["pending"] = i["kind"] == "entrada" and i["date"] > today
    order = {"entrada": 0, "saida": 1, "invest": 2, "transfer": 3}
    items.sort(key=lambda i: (i["date"], order[i["kind"]], i["description"].lower()))
    return items


def earliest_month(data: dict) -> str | None:
    found = [t["date"][:7] for t in data["transactions"]]
    found += [f["start_month"] for f in data["fixed"]]
    found += [i["first_date"][:7] for i in data["installments"]]
    found += [o["date"][:7] for o in data["ops"] if o["affects_cash"]]
    found += [d["date"][:7] for d in data["dividends"] if d["affects_cash"]]
    found += [m["date"][:7] for m in data["fi_moves"] if m["kind"] != "rendimento" and m["affects_cash"]]
    found += [t["date"][:7] for t in data["transfers"]]
    return min(found) if found else None


def start_month(data: dict, today_ym: str) -> str:
    return data["settings"].get("opening_month") or earliest_month(data) or today_ym


def zero_month(ym: str) -> dict:
    return {"ym": ym, "saldo_anterior": 0.0, "entradas": 0.0, "a_receber": 0.0, "saidas": 0.0, "a_pagar": 0.0,
            "aportes": 0.0, "resultado": 0.0, "resultado_previsto": 0.0, "saldo_final": 0.0, "saldo_previsto": 0.0}


def run(data: dict, upto_ym: str, today: str, account: int | None = None) -> list[dict]:
    """Saldo mês a mês, do primeiro mês do controle até upto_ym.

    O saldo final de cada mês vira o saldo anterior do mês seguinte. Só entram no saldo as
    entradas já recebidas (data até hoje) e as saídas já pagas; o que falta receber/pagar fica em
    'a_receber'/'a_pagar' e só aparece no 'saldo_previsto' (como ficará quando tudo for liquidado).
    """
    start = start_month(data, today[:7])
    if upto_ym < start:
        return []
    saldo = saldo_prev = round(sum(a["opening_balance"] for a in data["accounts"] if account in (None, a["id"])), 2)
    out = []
    ym = start
    while ym <= upto_ym:
        items = items_for_month(data, ym, today, account)
        entradas = sum(i["amount"] for i in items if i["kind"] == "entrada" and not i["pending"])
        a_receber = sum(i["amount"] for i in items if i["kind"] == "entrada" and i["pending"])
        saidas = sum(i["amount"] for i in items if i["kind"] == "saida" and not i["pending"])
        a_pagar = sum(i["amount"] for i in items if i["kind"] == "saida" and i["pending"])
        aportes = sum(i["amount"] for i in items if i["kind"] == "invest")
        resultado = entradas - saidas - aportes
        previsto = resultado + a_receber - a_pagar
        saldo_prev += previsto
        out.append({
            "ym": ym,
            "saldo_anterior": round(saldo, 2),
            "entradas": round(entradas, 2),
            "a_receber": round(a_receber, 2),
            "saidas": round(saidas, 2),
            "a_pagar": round(a_pagar, 2),
            "aportes": round(aportes, 2),
            "resultado": round(resultado, 2),
            "resultado_previsto": round(previsto, 2),
            "saldo_final": round(saldo + resultado, 2),
            "saldo_previsto": round(saldo_prev, 2),
        })
        saldo += resultado
        ym = ym_add(ym, 1)
    return out


def spending_by_category(items: list[dict]) -> list[dict]:
    """Saídas do mês por categoria (pagas ou não); a fatura é aberta nas categorias das compras."""
    cats: dict[str, float] = {}
    for i in items:
        if i["kind"] != "saida" or i.get("transfer") or i.get("invoice_payment"):
            continue  # pagamento de fatura não é gasto novo: as compras já contam nas categorias
        for part in i.get("parts") or [{"category": i["category"], "amount": i["amount"]}]:
            key = part["category"] or "Sem categoria"
            cats[key] = cats.get(key, 0.0) + part["amount"]
    return sorted(({"category": k, "total": round(t, 2)} for k, t in cats.items()), key=lambda c: -c["total"])


def month_view(conn, ym: str, today: str, account: int | None = None) -> dict:
    v.check_month(ym)
    data = load_all(conn)
    if account is not None:
        account = accounts.check_account(conn, account)
    year = ym[:4]
    months = {m["ym"]: m for m in run(data, f"{year}-12", today, account)}
    start = start_month(data, today[:7])
    summary = months.get(ym)
    outside = summary is None
    if outside:
        summary = zero_month(ym)
    items = items_for_month(data, ym, today, account)
    by_cat = spending_by_category(items)
    year_series = []
    for m in range(1, 13):
        key = f"{year}-{m:02d}"
        found = months.get(key)
        year_series.append({"ym": key, "saldo_final": found["saldo_final"] if found else None,
                            "saldo_previsto": found["saldo_previsto"] if found else None})
    return {
        "ym": ym, "summary": summary, "outside_control": outside, "start_month": start, "account": account,
        "accounts": [{"id": a["id"], "name": a["name"], "kind": a["kind"]} for a in data["accounts"]],
        "items": items, "by_category": by_cat, "budgets": budget_status(data["budgets"], by_cat),
        "year_series": year_series,
    }
