"""Contas (corrente, poupança, dinheiro…) e transferências entre elas."""
from __future__ import annotations

from . import db
from . import validate as v

KINDS = {"corrente": "Conta corrente", "poupanca": "Poupança", "dinheiro": "Dinheiro", "outra": "Outra"}


def default_id(conn) -> int:
    return conn.execute("SELECT id FROM accounts ORDER BY id LIMIT 1").fetchone()[0]


def check_account(conn, account_id) -> int:
    """Valida a conta informada; vazio = conta padrão (a primeira)."""
    if account_id in (None, "", 0, "0"):
        return default_id(conn)
    try:
        aid = int(account_id)
    except (TypeError, ValueError):
        raise ValueError("Conta inválida.") from None
    if conn.execute("SELECT 1 FROM accounts WHERE id = ?", (aid,)).fetchone() is None:
        raise ValueError("Conta não encontrada.")
    return aid


def save_account(conn, d: dict, id: int | None = None) -> int:
    from . import finance
    row = {
        "name": v.text(d, "name", "o nome da conta", maxlen=40),
        "kind": v.choice(d, "kind", "O tipo de conta", tuple(KINDS)) if d.get("kind") else "corrente",
        "opening_balance": round(v.number(d, "opening_balance", "O saldo inicial", allow_zero=True, allow_negative=True,
                                          default=0), 2),
    }
    clash = conn.execute("SELECT id FROM accounts WHERE name = ? COLLATE NOCASE", (row["name"],)).fetchone()
    if clash and clash[0] != id:
        raise ValueError("Já existe uma conta com esse nome.")
    return finance._save(conn, "accounts", row, id)


def usage(conn, account_id: int) -> int:
    n = 0
    for table in ("transactions", "fixed_items", "installments", "asset_ops", "dividends", "fixed_income_moves", "bank_imports"):
        n += conn.execute(f"SELECT COUNT(*) FROM {table} WHERE account_id = ?", (account_id,)).fetchone()[0]
    n += conn.execute("SELECT COUNT(*) FROM transfers WHERE from_account = ? OR to_account = ?", (account_id, account_id)).fetchone()[0]
    n += conn.execute("SELECT COUNT(*) FROM credit_cards WHERE pay_account_id = ?", (account_id,)).fetchone()[0]
    return n


def delete_account(conn, id: int) -> None:
    from . import finance
    if conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] <= 1:
        raise ValueError("É preciso manter pelo menos uma conta.")
    used = usage(conn, id)
    if used:
        raise ValueError(f"Esta conta tem {used} movimentação(ões) ligada(s). Mude-as de conta ou exclua antes.")
    finance._delete(conn, "accounts", id)


def list_accounts(conn, today: str) -> dict:
    """Contas com o saldo de hoje (só o que já entrou e saiu) e o previsto do mês."""
    from . import finance
    data = finance.load_all(conn)
    ym = today[:7]
    out = []
    for a in data["accounts"]:
        months = finance.run(data, ym, today, account=a["id"])
        cur = months[-1] if months else None
        out.append({**a, "kind_label": KINDS.get(a["kind"], a["kind"]),
                    "balance": cur["saldo_final"] if cur else a["opening_balance"],
                    "forecast": cur["saldo_previsto"] if cur else a["opening_balance"]})
    return {"items": out, "total": round(sum(a["balance"] for a in out), 2),
            "total_forecast": round(sum(a["forecast"] for a in out), 2), "default_id": data["default_account"]}


# ---------------------------------------------------------------- transferências

def save_transfer(conn, d: dict, id: int | None = None) -> int:
    from . import finance
    src = check_account(conn, d.get("from_account"))
    dst = check_account(conn, d.get("to_account"))
    if src == dst:
        raise ValueError("Escolha contas diferentes para a origem e o destino.")
    row = {"date": v.iso_date(d, "date"), "from_account": src, "to_account": dst,
           "amount": round(v.number(d, "amount", "O valor"), 2),
           "description": v.text(d, "description", "a descrição", required=False, maxlen=80)}
    return finance._save(conn, "transfers", row, id)


def delete_transfer(conn, id: int) -> None:
    from . import finance
    finance._delete(conn, "transfers", id)
