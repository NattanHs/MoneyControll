"""Importação de extratos OFX e conciliação com os lançamentos do app.

Conciliar = confirmar que uma linha do extrato do banco corresponde a algo que já está lançado (mesmo valor,
data próxima). Saídas conciliadas passam a contar como pagas. O que não tem correspondência pode ser lançado
a partir do extrato, vinculado à mão a outro lançamento ou ignorado. Importar o mesmo extrato duas vezes não
duplica nada (cada linha do banco tem um identificador único).
"""
from __future__ import annotations

import base64
import binascii
import itertools
import re
import unicodedata
from datetime import date, datetime

from . import accounts, cards, db, finance, ofx
from . import validate as v
from .common import amount_at, month_day, ym_add, ym_diff

WINDOW_DAYS = 7          # tolerância entre a data do banco e a do lançamento
SUSPECT_DAYS = 15        # mesmo valor até esta distância de dias já é suspeito de duplicidade (com nome parecido: até 31)
STATUSES = ("pendente", "conciliada", "criada", "ignorada")
VIEWS = ("pendente", "conciliada", "ignorada", "todas")
MAX_GROUP = 30           # máximo de linhas / lançamentos em uma conciliação em grupo
PAYABLE = ("lancamento", "fixa", "parcela", "fatura")


# ---------------------------------------------------------------- utilidades

def _raw(b64) -> bytes:
    try:
        return base64.b64decode(str(b64 or ""), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Arquivo inválido.") from None


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]+", " ", s)).strip()


def _tokens(s: str) -> set:
    return {w for w in _norm(s).split() if len(w) >= 3}


def _similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / max(1, min(len(ta), len(tb))) if ta and tb else 0.0


def _days(a: str, b: str) -> int:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def _outflow(item: dict) -> float:
    """Dinheiro que sai da conta (negativo = entra): saída +, entrada −, investimento já vem assinado."""
    if item["kind"] == "saida":
        return item["amount"]
    if item["kind"] == "entrada":
        return -item["amount"]
    return item["amount"]


def _key(item: dict) -> tuple:
    return (item["source"], item["ref_id"], item["ym"])


# ---------------------------------------------------------------- importação

def preview(conn, d: dict) -> dict:
    st = ofx.parse(_raw(d.get("data")))
    known = {r[0] for r in conn.execute("SELECT fitid FROM bank_txns WHERE account_key = ?", (st["account_key"],))}
    new = [t for t in st["txns"] if t["fitid"] not in known]
    return {
        "kind": st["kind"], "account_label": st["account_label"], "period_start": st["period_start"],
        "period_end": st["period_end"], "total": len(st["txns"]), "new": len(new), "duplicated": len(st["txns"]) - len(new),
        "inflow": round(sum(t["amount"] for t in new if t["amount"] > 0), 2),
        "outflow": round(-sum(t["amount"] for t in new if t["amount"] < 0), 2),
        "ledger_balance": st["ledger_balance"], "ledger_date": st["ledger_date"],
        "cards": db.rows(conn, "SELECT id, name FROM credit_cards ORDER BY name"),
        "accounts": db.rows(conn, "SELECT id, name FROM accounts ORDER BY id"),
        "account_id": _known_account(conn, st["account_key"]),
    }


def _known_account(conn, account_key: str) -> int:
    """Conta usada na última importação deste mesmo extrato (ou a conta padrão)."""
    row = conn.execute("SELECT account_id FROM bank_imports WHERE account_key = ? AND account_id IS NOT NULL "
                       "ORDER BY id DESC LIMIT 1", (account_key,)).fetchone()
    return row[0] if row else accounts.default_id(conn)


def import_ofx(conn, d: dict) -> dict:
    st = ofx.parse(_raw(d.get("data")))
    card_id = None
    account_id = None if st["kind"] == "cartao" else accounts.check_account(conn, d.get("account_id") or _known_account(conn, st["account_key"]))
    if st["kind"] == "cartao":
        if d.get("card_id") in (None, "", 0):
            raise ValueError("Este extrato é de cartão de crédito: escolha a qual cartão cadastrado ele pertence "
                             "(cadastre o cartão em Fixas e parcelas, se ainda não existir).")
        card_id = cards.check_card(conn, d.get("card_id"))
    name = v.text(d, "filename", "o nome do arquivo", required=False, maxlen=200) or "extrato.ofx"
    imp = conn.execute(
        "INSERT INTO bank_imports(filename, imported_at, account_key, account_label, kind, card_id, period_start, "
        "period_end, ledger_balance, ledger_date, account_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name, datetime.now().strftime("%Y-%m-%d %H:%M"), st["account_key"], st["account_label"], st["kind"], card_id,
         st["period_start"], st["period_end"], st["ledger_balance"], st["ledger_date"], account_id)).lastrowid
    new = ignored = 0
    for t in st["txns"]:
        # no cartão, valores positivos são pagamento da fatura/estorno: não são compras
        status = "ignorada" if st["kind"] == "cartao" and t["amount"] > 0 else "pendente"
        cur = conn.execute(
            "INSERT OR IGNORE INTO bank_txns(import_id, account_key, fitid, date, amount, memo, trntype, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (imp, st["account_key"], t["fitid"], t["date"], t["amount"], t["memo"], t["trntype"], status))
        if cur.rowcount:
            new += 1
            ignored += status == "ignorada"
    if new == 0:
        conn.execute("DELETE FROM bank_imports WHERE id = ?", (imp,))
        imp = None
    return {"import_id": imp, "new": new, "duplicated": len(st["txns"]) - new, "auto_ignored": ignored}


def delete_import(conn, id: int) -> None:
    if conn.execute("SELECT 1 FROM bank_imports WHERE id = ?", (id,)).fetchone() is None:
        raise ValueError("Importação não encontrada.")
    for r in db.rows(conn, "SELECT id FROM bank_txns WHERE import_id = ?", (id,)):
        if _links(conn, r["id"]):
            _undo(conn, _closure(conn, {r["id"]})[0], delete_created=False)  # desfaz o grupo inteiro; lançamentos criados ficam
    conn.execute("DELETE FROM bank_imports WHERE id = ?", (id,))  # remove as linhas junto (cascata)


# ---------------------------------------------------------------- candidatos

class _Ctx:
    """Dados carregados uma vez e itens do mês em cache."""

    def __init__(self, conn, today: str):
        self.conn, self.today = conn, today
        self.data = finance.load_all(conn)
        self._months: dict = {}
        self.used = {(r["source"], r["ref_id"], r["ym"]) for r in db.rows(conn, "SELECT source, ref_id, ym FROM bank_links")}

    def items(self, ym: str, account: int | None = None) -> list[dict]:
        if (ym, account) not in self._months:
            self._months[(ym, account)] = finance.items_for_month(self.data, ym, self.today, account)
        return self._months[(ym, account)]

    def pool(self, t: dict) -> list[dict]:
        """Lançamentos do app que poderiam corresponder à linha do extrato t."""
        ym = t["date"][:7]
        out = []
        if t["kind"] == "cartao":
            cid = t["card_id"]
            for tx in self.data["transactions"]:
                if tx.get("card_id") == cid and tx["kind"] == "saida":
                    out.append({"source": "lancamento", "ref_id": tx["id"], "ym": tx["date"][:7], "date": tx["date"],
                                "description": tx["description"], "category": tx["category"], "kind": "saida",
                                "amount": tx["amount"]})
            for i in self.data["installments"]:
                if i.get("card_id") != cid:
                    continue
                first, n = i["first_date"][:7], i["n_installments"]
                for pm in (ym_add(ym, -1), ym, ym_add(ym, 1)):
                    idx = ym_diff(first, pm)
                    if 0 <= idx < n:
                        out.append({"source": "parcela", "ref_id": i["id"], "ym": pm,
                                    "date": month_day(pm, int(i["first_date"][8:10])), "description": i["description"],
                                    "category": i["category"], "kind": "saida",
                                    "amount": amount_at(i["installment_amount"], i["changes"], pm)})
            return out
        acct = t.get("account_id") or self.data["default_account"]
        for m in (ym_add(ym, -1), ym, ym_add(ym, 1)):
            for it in self.items(m, acct):
                out.append({"source": it["source"], "ref_id": it["ref_id"], "ym": m, "date": it["date"],
                            "description": it["description"], "category": it["category"], "kind": it["kind"],
                            "amount": it["amount"]})
        return out

    def find(self, t: dict, source: str, ref_id: int, ym: str) -> dict | None:
        return next((c for c in self.pool(t) if (c["source"], c["ref_id"], c["ym"]) == (source, ref_id, ym)), None)


def _txn_rows(conn, where: str = "", params=()) -> list[dict]:
    return db.rows(conn, f"""
        SELECT b.*, i.kind AS kind, i.card_id AS card_id, i.account_id AS account_id,
               i.account_label AS account_label, i.filename AS filename
        FROM bank_txns b JOIN bank_imports i ON i.id = b.import_id {where}
        ORDER BY b.date DESC, b.id DESC""", params)


def _suggest(ctx: _Ctx, pending: list[dict]) -> dict:
    """Casamento automático: mesmo valor, data até WINDOW_DAYS de diferença, sentido igual.
    Cada lançamento serve para uma única linha do extrato; vence quem estiver mais perto na data.

    `ambiguous` > 0 quando há outra opção igualmente plausível (outro lançamento com o mesmo valor
    perto da data, ou outra linha do extrato disputando o mesmo lançamento): essas sugestões nunca
    são confirmadas em lote, só uma a uma pelo usuário."""
    pairs, cand, rivals = [], {}, {}
    for t in pending:
        for c in ctx.pool(t):
            if _key(c) in ctx.used or abs(_outflow(c) + t["amount"]) > 0.005:
                continue
            gap = _days(c["date"], t["date"])
            if gap <= WINDOW_DAYS:
                pairs.append((gap, -_similarity(t["memo"], c["description"]), t["id"], c))
                cand[t["id"]] = cand.get(t["id"], 0) + 1
                rivals.setdefault(_key(c), set()).add(t["id"])
    pairs.sort(key=lambda p: p[:3])
    taken_t, taken_k, out = set(), set(), {}
    for gap, _, tid, c in pairs:
        if tid in taken_t or _key(c) in taken_k:
            continue
        taken_t.add(tid)
        taken_k.add(_key(c))
        out[tid] = {**c, "days": gap, "ambiguous": (cand[tid] - 1) + (len(rivals[_key(c)]) - 1)}
    return out


def _suspects(ctx: _Ctx, t: dict, pending: list[dict] | None = None, hints: dict | None = None) -> list[dict]:
    """Motivos para desconfiar de que lançar esta linha como nova duplicaria algo que já existe."""
    out = []
    for c in ctx.pool(t):
        if _key(c) in ctx.used or (_outflow(c) > 0) != (t["amount"] < 0) or _outflow(c) == 0:
            continue
        gap = _days(c["date"], t["date"])
        diff = round(abs(_outflow(c)) - abs(t["amount"]), 2)
        same = abs(diff) < 0.005
        near_name = abs(diff) <= max(1.0, 0.1 * abs(t["amount"])) and gap <= 10 and _similarity(t["memo"], c["description"]) >= 0.5
        sim = _similarity(t["memo"], c["description"]) >= 0.5
        if (same and (gap <= SUSPECT_DAYS or (sim and gap <= 31))) or near_name:
            out.append({**c, "days": gap, "diff": diff,
                        "reason": "mesmo valor, já lançado no app" if same else "nome parecido e valor próximo, já lançado no app"})
    out.sort(key=lambda c: (abs(c["diff"]) > 0.005, c["days"]))
    out = out[:4]
    # a mesma linha vinda de outro extrato (ids diferentes) ou já lançada a partir dele
    for r in db.rows(ctx.conn, """SELECT memo, date FROM bank_txns WHERE import_id != ? AND amount = ?
                                  AND ABS(julianday(date) - julianday(?)) <= 1""", (t["import_id"], t["amount"], t["date"])):
        if _similarity(t["memo"], r["memo"]) >= 0.5:
            out.append({"source": None, "description": r["memo"], "date": r["date"], "amount": abs(t["amount"]),
                        "reason": "linha parecida em outro extrato importado"})
            break
    if hints is None:
        pend = pending if pending is not None else _txn_rows(ctx.conn, "WHERE b.status = 'pendente'")
        hints = _group_hints(ctx, pend, {})
    for h in hints.get(t["id"], []):
        names = " + ".join(f'{i["description"]} ({i["amount"]:.2f})' for i in h["items"]) if h["type"] == "um_para_varios" else \
            f'{h["items"][0]["description"]} ({h["items"][0]["amount"]:.2f}), pago em {len(h["txn_ids"])} linhas'
        out.append({"source": None, "description": names, "date": h["items"][0].get("date") or t["date"],
                    "amount": abs(t["amount"]), "reason": "a soma bate com lançamentos já existentes no app"})
    words = _tokens(t["memo"])
    if t["kind"] == "conta" and t["amount"] < 0 and ctx.data["cards"] and words & {"fatura", "cartao", "cartoes"}:
        out.append({"source": None, "description": "Pagamento de fatura de cartão?", "date": t["date"], "amount": abs(t["amount"]),
                    "reason": "parece pagamento de fatura: vincule à fatura do cartão em vez de lançar de novo"})
    return out


def _related(memo: str, descs: list[str]) -> bool:
    """Cada item precisa lembrar o nome do extrato ou outro item do grupo (ex.: duas dívidas da mesma pessoa)."""
    for n, d in enumerate(descs):
        if _similarity(memo, d) >= 0.5 or any(_similarity(d, o) >= 0.5 for m, o in enumerate(descs) if m != n):
            continue
        return False
    return True


def _usable(ctx: _Ctx, t: dict) -> list[dict]:
    out = []
    for c in ctx.pool(t):
        if _key(c) in ctx.used or (_outflow(c) > 0) != (t["amount"] < 0) or _outflow(c) == 0:
            continue
        if _days(c["date"], t["date"]) <= 31:
            out.append(c)
    out.sort(key=lambda c: (_days(c["date"], t["date"]), c["description"]))
    return out[:25]


def _group_hints(ctx: _Ctx, pending: list[dict], suggestions: dict) -> dict:
    """Sugestões de soma, sempre confirmadas uma a uma: uma linha do extrato = 2 ou 3 lançamentos do app,
    ou 2 linhas do extrato = 1 lançamento. Devolve {id da linha: [dicas]}."""
    out: dict = {}
    cents = lambda x: round(abs(x) * 100)
    for t in pending:
        if t["id"] in suggestions and not suggestions[t["id"]]["ambiguous"]:
            continue
        pool = _usable(ctx, t)
        found = False
        for size in (2, 3):
            for combo in itertools.combinations(pool, size):
                if abs(sum(_outflow(c) for c in combo) + t["amount"]) > 0.005 or len({_key(c) for c in combo}) < size:
                    continue
                if not _related(t["memo"], [c["description"] for c in combo]):
                    continue
                out.setdefault(t["id"], []).append({"type": "um_para_varios", "txn_ids": [t["id"]], "items": [
                    {k: c[k] for k in ("source", "ref_id", "ym", "description", "date", "amount", "kind")} for c in combo]})
                found = True
                break
            if found or len(out.get(t["id"], [])) >= 2:
                break
    # duas linhas do extrato somando um lançamento
    for a, b in itertools.combinations(pending, 2):
        if (a["amount"] < 0) != (b["amount"] < 0) or (a["kind"], a["card_id"]) != (b["kind"], b["card_id"]) or _days(a["date"], b["date"]) > 31:
            continue
        total = cents(a["amount"]) + cents(b["amount"])
        for c in _usable(ctx, a):
            if cents(_outflow(c)) != total or _key(c) in ctx.used or _days(c["date"], b["date"]) > 31:
                continue
            if _related(c["description"], [a["memo"], b["memo"]]) or _similarity(a["memo"], b["memo"]) >= 0.5:
                hint = {"type": "varios_para_um", "txn_ids": [a["id"], b["id"]], "items": [
                    {k: c[k] for k in ("source", "ref_id", "ym", "description", "date", "amount", "kind")}],
                    "txns": [{"id": x["id"], "memo": x["memo"], "date": x["date"], "amount": x["amount"]} for x in (a, b)]}
                for x in (a, b):
                    out.setdefault(x["id"], []).append(hint)
                break
    return out


def _learned_categories(conn) -> dict:
    out = {}
    for r in db.rows(conn, """SELECT b.memo, t.category FROM bank_txns b JOIN bank_links l ON l.txn_id = b.id
                              JOIN transactions t ON t.id = l.ref_id
                              WHERE l.source = 'lancamento' AND b.status IN ('criada', 'conciliada')
                              AND t.category != '' ORDER BY b.id"""):
        out[_norm(r["memo"])] = r["category"]
    return out


def _describe_item(ctx: _Ctx, source: str, ref_id: int, ym: str) -> dict | None:
    for it in ctx.items(ym) + [i for a in ctx.data["accounts"] for i in ctx.items(ym, a["id"]) if i["source"] in ("transf_saida", "transf_entrada")]:
        if (it["source"], it["ref_id"]) == (source, ref_id):
            return {"description": it["description"], "amount": it["amount"], "date": it["date"]}
    if source == "lancamento":
        for tx in ctx.data["transactions"]:
            if tx["id"] == ref_id:
                return {"description": tx["description"], "amount": tx["amount"], "date": tx["date"]}
    if source == "parcela":
        for i in ctx.data["installments"]:
            if i["id"] == ref_id:
                return {"description": i["description"], "amount": i["installment_amount"], "date": None}
    return None


# ---------------------------------------------------------------- vínculos (1:1, 1:N e N:1)

def _links(conn, tid: int) -> list[dict]:
    return db.rows(conn, "SELECT * FROM bank_links WHERE txn_id = ? ORDER BY id", (tid,))


def _closure(conn, tids: set) -> tuple[set, set]:
    """Todas as linhas do extrato e lançamentos ligados entre si (um pagamento de vários lançamentos,
    ou vários pagamentos de um lançamento): desfazer um deles desfaz o grupo inteiro."""
    txns, items = set(tids), set()
    while True:
        new_items = {(r["source"], r["ref_id"], r["ym"]) for t in txns for r in _links(conn, t)} - items
        items |= new_items
        new_txns = {r["txn_id"] for k in items for r in db.rows(
            conn, "SELECT txn_id FROM bank_links WHERE source = ? AND ref_id = ? AND ym = ?", k)} - txns
        if not new_items and not new_txns:
            return txns, items
        txns |= new_txns


def _undo(conn, tids: set, delete_created: bool = True) -> None:
    for tid in tids:
        t = _one(conn, tid)
        for l in _links(conn, tid):
            if l["marked"]:
                conn.execute("DELETE FROM payment_marks WHERE source = ? AND ref_id = ? AND ym = ?",
                             (l["source"], l["ref_id"], l["ym"]))
            if delete_created and t["status"] == "criada" and l["source"] == "lancamento":
                finance.delete_transaction(conn, l["ref_id"])
        conn.execute("DELETE FROM bank_links WHERE txn_id = ?", (tid,))
        conn.execute("UPDATE bank_txns SET status = 'pendente' WHERE id = ?", (tid,))


# ---------------------------------------------------------------- tela de conciliação

def app_balance_on(data: dict, day: str, account: int | None = None) -> float | None:
    """Saldo do app até `day` (de uma conta ou do total), contando só entradas recebidas e saídas pagas."""
    start = finance.start_month(data, day[:7])
    if day[:7] < start:
        return None
    saldo = sum(a["opening_balance"] for a in data["accounts"] if account in (None, a["id"]))
    ym = start
    while ym <= day[:7]:
        for it in finance.items_for_month(data, ym, day, account):
            if it["date"] > day or it["pending"]:
                continue
            saldo += it["amount"] if it["kind"] == "entrada" else -it["amount"] if it["kind"] == "saida" else (
                -it["amount"] if it["kind"] == "invest" else 0)
        ym = ym_add(ym, 1)
    return round(saldo, 2)


def overview(conn, view: str, today: str) -> dict:
    if view not in VIEWS:
        raise ValueError("Visão inválida.")
    ctx = _Ctx(conn, today)
    counts = {s: 0 for s in STATUSES}
    for r in conn.execute("SELECT status, COUNT(*) FROM bank_txns GROUP BY status"):
        counts[r[0]] = r[1]
    pending_all = _txn_rows(conn, "WHERE b.status = 'pendente'")
    suggestions = _suggest(ctx, pending_all)
    learned = _learned_categories(conn)
    if view == "todas":
        rows = _txn_rows(conn)
    elif view == "conciliada":
        rows = _txn_rows(conn, "WHERE b.status IN ('conciliada', 'criada')")
    else:
        rows = _txn_rows(conn, "WHERE b.status = ?", (view,))
    links_of: dict = {}
    for l in db.rows(conn, "SELECT * FROM bank_links ORDER BY id"):
        links_of.setdefault(l["txn_id"], []).append(l)
    hints = _group_hints(ctx, pending_all, suggestions)
    suspects = {t["id"]: _suspects(ctx, t, pending_all, hints) for t in pending_all}
    for r in rows:
        r["suggestion"] = suggestions.get(r["id"]) if r["status"] == "pendente" else None
        r["suspects"] = suspects.get(r["id"], []) if r["status"] == "pendente" else []
        r["suggested_category"] = learned.get(_norm(r["memo"]), "") if r["status"] == "pendente" else ""
        r["matched_items"], r["group_size"] = [], 0
        if r["status"] in ("conciliada", "criada"):
            r["matched_items"] = [d for d in (_describe_item(ctx, l["source"], l["ref_id"], l["ym"]) for l in links_of.get(r["id"], [])) if d]
            group = _closure(conn, {r["id"]})
            r["group_size"] = len(group[0]) if len(group[0]) > 1 or len(group[1]) > 1 else 0
            r["group_txns"] = len(group[0])
        r["matched_description"] = " + ".join(d["description"] for d in r["matched_items"]) or None
        r["group_hints"] = hints.get(r["id"], []) if r["status"] == "pendente" else []
    imports = db.rows(conn, """SELECT i.*, (SELECT COUNT(*) FROM bank_txns b WHERE b.import_id = i.id) AS total,
                               (SELECT COUNT(*) FROM bank_txns b WHERE b.import_id = i.id AND b.status = 'pendente') AS pending
                               FROM bank_imports i ORDER BY i.id DESC LIMIT 20""")
    checks = []
    default = ctx.data["default_account"]
    names = {x["id"]: x["name"] for x in ctx.data["accounts"]}
    seen = set()
    for last in db.rows(conn, "SELECT * FROM bank_imports WHERE kind = 'conta' AND ledger_balance IS NOT NULL AND ledger_date IS NOT NULL "
                              "ORDER BY ledger_date DESC, id DESC"):
        acct = last["account_id"] or default
        if acct in seen:
            continue
        seen.add(acct)
        app = app_balance_on(ctx.data, last["ledger_date"], acct)
        if app is not None:
            checks.append({"date": last["ledger_date"], "bank": last["ledger_balance"], "app": app,
                           "diff": round(last["ledger_balance"] - app, 2), "account": last["account_label"],
                           "account_id": acct, "account_name": names.get(acct, "")})
    check = checks[0] if checks else None
    return {"view": view, "counts": counts, "rows": rows, "imports": imports, "balance_check": check, "balance_checks": checks,
            "accounts": [{"id": x["id"], "name": x["name"]} for x in ctx.data["accounts"]],
            "suggested": sum(1 for g in suggestions.values() if not g["ambiguous"]),
            "to_review": sum(1 for t in pending_all if (suggestions.get(t["id"], {}).get("ambiguous"))
                             or (t["id"] not in suggestions and suspects[t["id"]])),
            "unmatched": sum(1 for t in pending_all if t["id"] not in suggestions and not suspects[t["id"]])}


def candidates(conn, tid: int, today: str) -> list[dict]:
    t = _one(conn, tid)
    ctx = _Ctx(conn, today)
    out = []
    for c in ctx.pool(t):
        if _key(c) in ctx.used:
            continue
        if (_outflow(c) > 0) != (t["amount"] < 0) or _outflow(c) == 0:  # precisa ter o mesmo sentido
            continue
        gap = _days(c["date"], t["date"])
        if gap > 31:
            continue
        diff = round(abs(_outflow(c)) - abs(t["amount"]), 2)
        out.append({**c, "days": gap, "exact": abs(diff) < 0.005, "diff": diff})
    out.sort(key=lambda c: (not c["exact"], abs(c["diff"]) if not c["exact"] else 0, c["days"]))
    return out[:40]


# ---------------------------------------------------------------- ações

def _one(conn, tid: int) -> dict:
    rows = _txn_rows(conn, "WHERE b.id = ?", (tid,))
    if not rows:
        raise ValueError("Linha do extrato não encontrada.")
    return rows[0]


def _settle(conn, item: dict, tdate: str, tkind: str, today: str, note: list) -> tuple[str, int]:
    """Efeitos de conciliar um lançamento: entrada futura vira a data do banco; saída vira paga."""
    ym = item["ym"]
    if item["source"] == "lancamento" and item["kind"] == "entrada" and item["date"] > today >= tdate:
        conn.execute("UPDATE transactions SET date = ? WHERE id = ?", (tdate, item["ref_id"]))  # o dinheiro já entrou
        ym = tdate[:7]
        note.append("data ajustada para a do banco")
    marked = 0
    if tkind == "conta" and item["kind"] == "saida" and item["source"] in PAYABLE:
        finance.set_payment(conn, {"source": item["source"], "ref_id": item["ref_id"], "ym": ym, "paid": True}, today)
        marked = int(conn.execute("SELECT 1 FROM payment_marks WHERE source = ? AND ref_id = ? AND ym = ?",
                                  (item["source"], item["ref_id"], ym)).fetchone() is not None)
    return ym, marked


def _link(conn, tid: int, status: str, source: str, ref_id: int, ym: str, marked: int) -> None:
    conn.execute("UPDATE bank_txns SET status = ? WHERE id = ?", (status, tid))
    conn.execute("INSERT INTO bank_links(txn_id, source, ref_id, ym, marked) VALUES (?, ?, ?, ?, ?)",
                 (tid, source, ref_id, ym, marked))


def reconcile(conn, tid: int, d: dict, today: str, ctx: _Ctx | None = None) -> dict:
    t = _one(conn, tid)
    if t["status"] != "pendente":
        raise ValueError("Esta linha do extrato já foi tratada.")
    source = v.choice(d, "source", "A origem", ("lancamento", "fixa", "parcela", "fatura", "dividendo", "invest", "transf_saida", "transf_entrada"))
    ref_id, ym = v.integer(d, "ref_id", "O lançamento"), v.month(d, "ym", "o mês")
    ctx = ctx or _Ctx(conn, today)
    item = ctx.find(t, source, ref_id, ym)
    if item is None:
        raise ValueError("Lançamento não encontrado (ele pode ter sido alterado ou excluído).")
    if _key(item) in ctx.used:
        raise ValueError("Esse lançamento já está conciliado com outra linha do extrato.")
    if (_outflow(item) > 0) != (t["amount"] < 0):
        raise ValueError("O sentido não confere: uma entrada não pode ser conciliada com uma saída.")
    note: list = []
    if item["source"] == "lancamento" and v.boolean(d, "fix_amount", False) and abs(abs(t["amount"]) - item["amount"]) >= 0.005:
        conn.execute("UPDATE transactions SET amount = ? WHERE id = ?", (round(abs(t["amount"]), 2), ref_id))
        note.append("valor ajustado")
    ym, marked = _settle(conn, item, t["date"], t["kind"], today, note)
    _link(conn, tid, "conciliada", item["source"], ref_id, ym, marked)
    ctx.used.add((item["source"], ref_id, ym))
    return {"notes": note}


def group_candidates(conn, tids: list, today: str) -> dict:
    """Lançamentos do app que podem compor, somados, o valor das linhas do extrato escolhidas."""
    txns = _group_txns(conn, tids)
    ctx = _Ctx(conn, today)
    seen, out = set(), []
    for t in txns:
        for c in ctx.pool(t):
            k = _key(c)
            if k in seen or k in ctx.used or (_outflow(c) > 0) != (t["amount"] < 0) or _outflow(c) == 0:
                continue
            gap = min(_days(c["date"], x["date"]) for x in txns)
            if gap > 31:
                continue
            seen.add(k)
            out.append({**c, "days": gap})
    out.sort(key=lambda c: (c["days"], c["description"].lower()))
    return {"total": round(-sum(t["amount"] for t in txns), 2), "txns": [
        {"id": t["id"], "memo": t["memo"], "date": t["date"], "amount": t["amount"]} for t in txns], "items": out[:80]}


def _group_txns(conn, tids) -> list[dict]:
    if not isinstance(tids, list) or not tids:
        raise ValueError("Escolha ao menos uma linha do extrato.")
    ids = []
    for x in tids:
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            raise ValueError("Linha do extrato inválida.") from None
    if len(set(ids)) != len(ids):
        raise ValueError("Há linhas repetidas na seleção.")
    if len(ids) > MAX_GROUP:
        raise ValueError(f"Escolha no máximo {MAX_GROUP} linhas por vez.")
    txns = [_one(conn, i) for i in ids]
    if any(t["status"] != "pendente" for t in txns):
        raise ValueError("Uma das linhas já foi tratada.")
    if len({t["amount"] < 0 for t in txns}) > 1:
        raise ValueError("Não misture saídas e entradas na mesma conciliação.")
    if len({(t["kind"], t["card_id"]) for t in txns}) > 1:
        raise ValueError("Não misture extratos de contas/cartões diferentes na mesma conciliação.")
    return txns


def reconcile_group(conn, d: dict, today: str) -> dict:
    """Um pagamento para vários lançamentos (ex.: duas dívidas da mesma pessoa) ou vários pagamentos para
    um lançamento. A soma dos dois lados precisa ser exatamente igual."""
    txns = _group_txns(conn, d.get("txn_ids"))
    refs = d.get("items")
    if not isinstance(refs, list) or not refs:
        raise ValueError("Escolha ao menos um lançamento do app.")
    if len(refs) > MAX_GROUP:
        raise ValueError(f"Escolha no máximo {MAX_GROUP} lançamentos por vez.")
    ctx = _Ctx(conn, today)
    items, keys = [], set()
    for r in refs:
        if not isinstance(r, dict):
            raise ValueError("Lançamento inválido.")
        source = v.choice(r, "source", "A origem", ("lancamento", "fixa", "parcela", "fatura", "dividendo", "invest", "transf_saida", "transf_entrada"))
        ref_id, ym = v.integer(r, "ref_id", "O lançamento"), v.month(r, "ym", "o mês")
        if (source, ref_id, ym) in keys:
            raise ValueError("Há lançamentos repetidos na seleção.")
        keys.add((source, ref_id, ym))
        item = next((c for t in txns for c in ctx.pool(t) if _key(c) == (source, ref_id, ym)), None)
        if item is None:
            raise ValueError("Lançamento não encontrado (ele pode ter sido alterado ou excluído).")
        if _key(item) in ctx.used:
            raise ValueError(f'"{item["description"]}" já está conciliado com outra linha do extrato.')
        if (_outflow(item) > 0) != (txns[0]["amount"] < 0) or _outflow(item) == 0:
            raise ValueError(f'O sentido de "{item["description"]}" não confere com o do extrato.')
        items.append(item)
    bank_total = round(-sum(t["amount"] for t in txns), 2)
    app_total = round(sum(_outflow(c) for c in items), 2)
    if abs(bank_total - app_total) > 0.005:
        raise ValueError(f"A soma não bate: extrato {abs(bank_total):.2f} x lançamentos {abs(app_total):.2f} "
                         f"(diferença de {abs(bank_total - app_total):.2f}).".replace(".", ","))
    latest = max(t["date"] for t in txns)
    note: list = []
    resolved = []
    for item in items:
        ym, marked = _settle(conn, item, latest, txns[0]["kind"], today, note)
        resolved.append((item["source"], item["ref_id"], ym, marked))
    for n, t in enumerate(txns):
        conn.execute("UPDATE bank_txns SET status = 'conciliada' WHERE id = ?", (t["id"],))
        for source, ref_id, ym, marked in resolved:
            conn.execute("INSERT INTO bank_links(txn_id, source, ref_id, ym, marked) VALUES (?, ?, ?, ?, ?)",
                         (t["id"], source, ref_id, ym, marked if n == 0 else 0))
    return {"txns": len(txns), "items": len(items), "notes": note}


def unreconcile(conn, tid: int, today: str) -> dict:
    t = _one(conn, tid)
    if t["status"] not in ("conciliada", "criada"):
        raise ValueError("Esta linha não está conciliada.")
    group, _ = _closure(conn, {tid})
    _undo(conn, group)  # em grupo, desfaz todas as linhas e lançamentos ligados
    return {"undone": len(group)}


def create_from(conn, tid: int, d: dict, today: str, learned: dict | None = None, checked: bool = False) -> dict:
    t = _one(conn, tid)
    if t["status"] != "pendente":
        raise ValueError("Esta linha do extrato já foi tratada.")
    if not checked and not v.boolean(d, "force", False):
        found = _suspects(_Ctx(conn, today), t)
        if found:  # nada é criado: o usuário precisa confirmar ("lançar mesmo assim") ou vincular ao que já existe
            return {"duplicate_suspected": True, "suspects": found}
    kind = "entrada" if t["amount"] > 0 else "saida"
    learned = learned if learned is not None else _learned_categories(conn)
    category = v.text(d, "category", "a categoria", required=False, maxlen=40) if "category" in d \
        else learned.get(_norm(t["memo"]), "")
    description = v.text(d, "description", "a descrição") if d.get("description") else t["memo"][:120]
    tx = finance.save_transaction(conn, {
        "date": t["date"], "kind": kind, "description": description, "category": category,
        "amount": abs(t["amount"]), "card_id": t["card_id"] if t["kind"] == "cartao" and kind == "saida" else None,
        "account_id": t["account_id"]})
    _link(conn, tid, "criada", "lancamento", tx, t["date"][:7], 0)
    return {"transaction_id": tx}


def set_ignored(conn, tid: int, ignored: bool) -> None:
    t = _one(conn, tid)
    want, other = ("ignorada", "pendente") if ignored else ("pendente", "ignorada")
    if t["status"] != other:
        raise ValueError("Esta linha do extrato já foi tratada." if ignored else "Esta linha não está ignorada.")
    conn.execute("UPDATE bank_txns SET status = ? WHERE id = ?", (want, tid))


def reconcile_all(conn, today: str) -> dict:
    """Concilia só as sugestões sem dúvida. As ambíguas ficam para o usuário confirmar uma a uma."""
    ctx = _Ctx(conn, today)
    pending = _txn_rows(conn, "WHERE b.status = 'pendente'")
    done = failed = skipped = 0
    for tid, c in _suggest(ctx, pending).items():
        if c["ambiguous"]:
            skipped += 1
            continue
        try:
            reconcile(conn, tid, {"source": c["source"], "ref_id": c["ref_id"], "ym": c["ym"]}, today, ctx)
            done += 1
        except ValueError:
            failed += 1
    return {"reconciled": done, "failed": failed, "skipped": skipped}


def create_all(conn, today: str) -> dict:
    """Lança como novos os pendentes sem correspondência E sem suspeita de duplicidade."""
    ctx = _Ctx(conn, today)
    pending = _txn_rows(conn, "WHERE b.status = 'pendente'")
    matched = _suggest(ctx, pending)
    learned = _learned_categories(conn)
    created = skipped = 0
    hints = _group_hints(ctx, pending, matched)
    for t in pending:
        if t["id"] in matched:
            continue
        if _suspects(ctx, t, pending, hints):
            skipped += 1
            continue
        create_from(conn, t["id"], {}, today, learned, checked=True)
        learned = _learned_categories(conn)
        ctx.used.add(("lancamento", conn.execute("SELECT ref_id FROM bank_links WHERE txn_id = ?", (t["id"],)).fetchone()[0],
                      t["date"][:7]))
        created += 1
    return {"created": created, "skipped": skipped}
