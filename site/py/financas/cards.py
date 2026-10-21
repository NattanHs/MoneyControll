"""Cartões de crédito: as compras entram na fatura que fecha/vence conforme o dia de fechamento.

Regra: compra feita até o dia de fechamento (inclusive) entra na fatura que fecha naquele mês;
depois do fechamento, na fatura do mês seguinte. O dinheiro só sai do caixa no vencimento.
"""
from __future__ import annotations

from . import accounts, db
from . import validate as v
from .common import amount_at, month_day, ym_add, ym_diff


def closing_ym(card: dict, date_iso: str) -> str:
    ym = date_iso[:7]
    return ym if int(date_iso[8:10]) <= card["closing_day"] else ym_add(ym, 1)


def due_ym(card: dict, date_iso: str) -> str:
    """Mês em que vence a fatura que recebe uma compra feita em date_iso."""
    closing = closing_ym(card, date_iso)
    return closing if card["due_day"] > card["closing_day"] else ym_add(closing, 1)


def closing_month_for_due(card: dict, due: str) -> str:
    return due if card["due_day"] > card["closing_day"] else ym_add(due, -1)


def statement_lines(data: dict, card: dict, ym: str) -> list[dict]:
    """Compras e parcelas que compõem a fatura do cartão que vence em ym."""
    lines = []
    for t in data["transactions"]:
        if t.get("card_id") == card["id"] and t["kind"] == "saida" and due_ym(card, t["date"]) == ym:
            lines.append({"source": "lancamento", "ref_id": t["id"], "date": t["date"], "description": t["description"],
                          "category": t["category"], "amount": t["amount"], "tag": ""})
    for i in data["installments"]:
        if i.get("card_id") != card["id"]:
            continue
        first, day, n = i["first_date"][:7], int(i["first_date"][8:10]), i["n_installments"]
        diff = ym_diff(first, ym)
        for idx in range(max(0, diff - 2), min(n, diff + 1)):  # a parcela cai de 0 a 2 meses antes do vencimento
            pm = ym_add(first, idx)
            pdate = month_day(pm, day)
            if due_ym(card, pdate) == ym:
                lines.append({"source": "parcela", "ref_id": i["id"], "date": pdate, "description": i["description"],
                              "category": i["category"], "amount": amount_at(i["installment_amount"], i["changes"], pm),
                              "tag": f"{idx + 1}/{n}"})
    lines.sort(key=lambda l: (l["date"], l["description"].lower()))
    return lines


def statement_item(data: dict, card: dict, ym: str) -> dict | None:
    lines = statement_lines(data, card, ym)
    if not lines:
        return None
    parts: dict[str, float] = {}
    for l in lines:
        parts[l["category"]] = parts.get(l["category"], 0.0) + l["amount"]
    return {"source": "fatura", "ref_id": card["id"], "date": month_day(ym, card["due_day"]),
            "description": f"Fatura {card['name']}", "category": "Cartão de crédito", "kind": "saida",
            "amount": round(sum(l["amount"] for l in lines), 2), "tag": f"{len(lines)} compra(s)",
            "parts": [{"category": k, "amount": round(a, 2)} for k, a in parts.items()]}


# ---------------------------------------------------------------- cadastro

def save_card(conn, d: dict, id: int | None = None) -> int:
    from . import finance
    row = {
        "name": v.text(d, "name", "o nome do cartão", maxlen=40),
        "closing_day": v.integer(d, "closing_day", "O dia de fechamento", 1, 31),
        "due_day": v.integer(d, "due_day", "O dia de vencimento", 1, 31),
        "pay_account_id": accounts.check_account(conn, d.get("pay_account_id")),
    }
    return finance._save(conn, "credit_cards", row, id)


def card_usage(conn, card_id: int) -> int:
    n = conn.execute("SELECT COUNT(*) FROM transactions WHERE card_id = ?", (card_id,)).fetchone()[0]
    return n + conn.execute("SELECT COUNT(*) FROM installments WHERE card_id = ?", (card_id,)).fetchone()[0]


def delete_card(conn, id: int) -> None:
    from . import finance
    used = card_usage(conn, id)
    if used:
        raise ValueError(f"Este cartão tem {used} compra(s) lançada(s). Exclua ou mude essas compras antes.")
    conn.execute("DELETE FROM payment_marks WHERE source = 'fatura' AND ref_id = ?", (id,))
    finance._delete(conn, "credit_cards", id)


def check_card(conn, card_id) -> int | None:
    """Valida o cartão informado em um lançamento (vazio = sem cartão)."""
    if card_id in (None, "", 0, "0"):
        return None
    try:
        cid = int(card_id)
    except (TypeError, ValueError):
        raise ValueError("Cartão inválido.") from None
    if conn.execute("SELECT 1 FROM credit_cards WHERE id = ?", (cid,)).fetchone() is None:
        raise ValueError("Cartão não encontrado.")
    return cid


def default_paid(card: dict, ym: str, today: str) -> bool:
    return month_day(ym, card["due_day"]) <= today


def list_cards(conn, today: str) -> list[dict]:
    from . import finance
    data = finance.load_all(conn)
    out = []
    for card in data["cards"]:
        open_ym = due_ym(card, today)
        sts = [ym_add(open_ym, k) for k in (-1, 0, 1)]
        statements = []
        for ym in sts:
            lines = statement_lines(data, card, ym)
            total = round(sum(l["amount"] for l in lines), 2)
            if total or ym == open_ym:
                paid = data["marks"].get(("fatura", card["id"], ym))
                statements.append({"ym": ym, "total": total, "count": len(lines),
                                   "due_date": month_day(ym, card["due_day"]),
                                   "paid": default_paid(card, ym, today) if paid is None else bool(paid),
                                   "open": ym == open_ym})
        out.append({**card, "open_ym": open_ym, "statements": statements})
    return out


def statement(conn, card_id: int, ym: str, today: str) -> dict:
    from . import finance
    v.check_month(ym)
    data = finance.load_all(conn)
    card = next((c for c in data["cards"] if c["id"] == card_id), None)
    if card is None:
        raise ValueError("Cartão não encontrado.")
    lines = statement_lines(data, card, ym)
    mark = data["marks"].get(("fatura", card_id, ym))
    return {
        "card": card, "ym": ym,
        "due_date": month_day(ym, card["due_day"]),
        "closing_date": month_day(closing_month_for_due(card, ym), card["closing_day"]),
        "total": round(sum(l["amount"] for l in lines), 2),
        "paid": default_paid(card, ym, today) if mark is None else bool(mark),
        "lines": lines,
    }
