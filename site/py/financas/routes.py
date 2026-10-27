"""Rotas da API do MoneyControl no celular.

É a mesma lista de rotas da versão de computador (server.py), sem o que só faz sentido lá
(servidor HTTP, PIN e rede, licença, pasta externa de backup). Aqui não há servidor: o navegador
chama `api.dispatch()` direto, dentro do próprio aparelho.
"""
from __future__ import annotations

import re
import sqlite3
import tempfile
from datetime import date
from pathlib import Path

from . import accounts, analysis, backup, bank, cards, db, finance, invest, onboarding, reports, syncmeta

# o "servidor" que os handlers recebem: só precisa saber onde está o banco e que dia é hoje
class Ctx:
    def __init__(self, db_path, today: str | None = None):
        self.db_path = Path(db_path)
        self._today = today

    @property
    def today(self) -> str:
        return self._today or date.today().isoformat()


def ok() -> dict:
    return {"ok": True}


# ---------------------------------------------------------------- rotas da API
# cada handler recebe (conexão, match, corpo_json, contexto)

def _recurring(c, m, b, s):
    ym = s.today[:7]
    return {"today_ym": ym, "fixed": finance.list_fixed(c, ym),
            "installments": finance.list_installments(c, ym), "cards": cards.list_cards(c, s.today)}


def _backup(c, m, b, s):
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "backup.db"
        target = sqlite3.connect(dest)
        c.backup(target)
        target.close()
        return {"__file__": dest.read_bytes(), "name": f"financas-backup-{s.today}.db",
                "ctype": "application/octet-stream"}


def _backups(c, m, b, s):
    return {"items": backup.list_backups(s.db_path)}


def _backup_now(c, m, b, s):
    return {"name": backup.manual_backup(c, s.db_path).name}


def _report(c, m, b, s):
    start, end, acc, fmt = m.group(1), m.group(2), m.group(3), m.group(4)
    rep = analysis.report(c, start, end, s.today, None if acc == "all" else acc)
    if not fmt:
        return rep
    base = f"MoneyControl-relatorio-{start}-a-{end}"
    if fmt == "xlsx":
        return {"__file__": analysis.to_xlsx(rep), "name": base + ".xlsx",
                "ctype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    return {"__file__": analysis.to_csv(rep), "name": base + ".csv", "ctype": "text/csv; charset=utf-8"}


def _restore(c, m, b, s):
    return backup.restore(c, s.db_path, backup.backup_path(s.db_path, b.get("name")))


def _restore_upload(c, m, b, s):
    return backup.restore_upload(c, s.db_path, b.get("data"))


def _inspect_upload(c, m, b, s):
    return backup.inspect_upload(c, b.get("data"))


def _inspect_backup(c, m, b, s):
    return backup.inspect_backup(c, backup.backup_path(s.db_path, b.get("name")))


def _sync_state(c, m, b, s):
    return syncmeta.state(c)


def _sync_stamp(c, m, b, s):
    absorb = b.get("absorb") or []
    if not isinstance(absorb, list):
        raise ValueError("Linhagem inválida.")
    return syncmeta.stamp(c, [str(v) for v in absorb], bool(b.get("force")))


def _sync_adopt(c, m, b, s):
    return backup.adopt(c, s.db_path, b.get("data"), b.get("expect_head"), bool(b.get("force")))


def _reset(c, m, b, s):
    return backup.reset(c, s.db_path, b)


def _create_tx(c, b):
    """Cria um lançamento; `client_ref` evita duplicar se o mesmo envio for repetido."""
    ref = str(b.pop("client_ref", "") or "")[:64]
    if ref:
        row = c.execute("SELECT row_id FROM client_refs WHERE ref = ?", (ref,)).fetchone()
        if row:
            return {"id": row[0], "duplicate": True}
    new_id = finance.save_transaction(c, b)
    if ref:
        c.execute("INSERT INTO client_refs(ref, row_id) VALUES (?, ?)", (ref, new_id))
    return {"id": new_id}


def _do(fn, *pre):
    def handler(c, m, b, s):
        fn(c, *pre, *[int(g) for g in m.groups()])
        return ok()
    return handler


def _create(saver):
    return lambda c, m, b, s: {"id": saver(c, b)}


def _update(saver):
    return lambda c, m, b, s: (saver(c, b, int(m.group(1))), ok())[1]


def _no_license(c, m, b, s):
    # sem licença: o app é seu, nunca expira nem pede chave
    return {"enabled": False, "blocked": False, "state": "livre"}


ROUTES = [
    ("GET", r"/api/overview", lambda c, m, b, s: reports.overview(c, s.today)),
    ("GET", r"/api/month/(\d{4}-\d{2})", lambda c, m, b, s: finance.month_view(c, m.group(1), s.today)),
    ("GET", r"/api/month/(\d{4}-\d{2})/account/(\d+)", lambda c, m, b, s: finance.month_view(c, m.group(1), s.today, int(m.group(2)))),
    ("GET", r"/api/accounts", lambda c, m, b, s: accounts.list_accounts(c, s.today)),
    ("POST", r"/api/accounts", _create(accounts.save_account)),
    ("PUT", r"/api/accounts/(\d+)", _update(accounts.save_account)),
    ("DELETE", r"/api/accounts/(\d+)", _do(accounts.delete_account)),
    ("POST", r"/api/transfers", _create(accounts.save_transfer)),
    ("PUT", r"/api/transfers/(\d+)", _update(accounts.save_transfer)),
    ("DELETE", r"/api/transfers/(\d+)", _do(accounts.delete_transfer)),
    ("GET", r"/api/categories", lambda c, m, b, s: finance.categories(c)),
    ("GET", r"/api/recurring", _recurring),
    ("GET", r"/api/invest", lambda c, m, b, s: reports.invest_dashboard(c, s.today)),
    ("GET", r"/api/settings", lambda c, m, b, s: reports.get_settings(c)),
    ("PUT", r"/api/settings", lambda c, m, b, s: reports.save_settings(c, b)),
    ("GET", r"/api/backup", _backup),
    ("GET", r"/api/backups", _backups),
    ("POST", r"/api/backups", _backup_now),
    ("GET", r"/api/report/(\d{4}-\d{2})/(\d{4}-\d{2})/(all|\d+)(?:/(xlsx|csv))?", _report),
    ("POST", r"/api/backups/restore", _restore),
    ("POST", r"/api/backups/restore-upload", _restore_upload),
    ("POST", r"/api/backups/inspect-upload", _inspect_upload),
    ("POST", r"/api/backups/inspect", _inspect_backup),
    ("GET", r"/api/sync/state", _sync_state),
    ("POST", r"/api/sync/stamp", _sync_stamp),
    ("POST", r"/api/sync/adopt", _sync_adopt),
    ("POST", r"/api/reset", _reset),
    ("GET", r"/api/license", _no_license),
    ("GET", r"/api/wizard", lambda c, m, b, s: {"needed": onboarding.needed(c)}),
    ("POST", r"/api/wizard", lambda c, m, b, s: onboarding.finish(c, b)),
    ("POST", r"/api/wizard/skip", lambda c, m, b, s: onboarding.skip(c)),
    ("GET", r"/api/suggestions", lambda c, m, b, s: finance.suggestions(c)),
    # pago / a pagar
    ("POST", r"/api/payments", lambda c, m, b, s: finance.set_payment(c, b, s.today)),
    # quitar antes do prazo: um lançamento aponta o débito (ou recebimento) previsto que ele paga
    ("GET", r"/api/link-options/(entrada|saida)(?:/(\d+))?",
     lambda c, m, b, s: finance.link_options(c, m.group(1), s.today, int(m.group(2)) if m.group(2) else None)),
    ("POST", r"/api/links/candidates", lambda c, m, b, s: finance.link_candidates(c, b, s.today)),
    ("POST", r"/api/links", lambda c, m, b, s: finance.add_link(c, b)),
    ("DELETE", r"/api/links/(\d+)", _do(finance.remove_link)),
    # orçamento por categoria
    ("GET", r"/api/budgets", lambda c, m, b, s: finance.list_budgets(c)),
    ("POST", r"/api/budgets", _create(finance.save_budget)),
    ("PUT", r"/api/budgets/(\d+)", _update(finance.save_budget)),
    ("DELETE", r"/api/budgets/(\d+)", _do(finance.delete_budget)),
    # extrato bancário (OFX) e conciliação
    ("POST", r"/api/bank/preview", lambda c, m, b, s: bank.preview(c, b)),
    ("POST", r"/api/bank/import", lambda c, m, b, s: bank.import_ofx(c, b)),
    ("GET", r"/api/bank/list/(pendente|conciliada|ignorada|todas)", lambda c, m, b, s: bank.overview(c, m.group(1), s.today)),
    ("GET", r"/api/bank/txns/(\d+)/candidates", lambda c, m, b, s: bank.candidates(c, int(m.group(1)), s.today)),
    ("POST", r"/api/bank/txns/(\d+)/reconcile", lambda c, m, b, s: bank.reconcile(c, int(m.group(1)), b, s.today)),
    ("POST", r"/api/bank/txns/(\d+)/unreconcile", lambda c, m, b, s: bank.unreconcile(c, int(m.group(1)), s.today)),
    ("POST", r"/api/bank/txns/(\d+)/create", lambda c, m, b, s: bank.create_from(c, int(m.group(1)), b, s.today)),
    ("POST", r"/api/bank/txns/(\d+)/ignore", lambda c, m, b, s: (bank.set_ignored(c, int(m.group(1)), True), ok())[1]),
    ("POST", r"/api/bank/txns/(\d+)/restore", lambda c, m, b, s: (bank.set_ignored(c, int(m.group(1)), False), ok())[1]),
    ("POST", r"/api/bank/group/candidates", lambda c, m, b, s: bank.group_candidates(c, b.get("txn_ids"), s.today)),
    ("POST", r"/api/bank/group/reconcile", lambda c, m, b, s: bank.reconcile_group(c, b, s.today)),
    ("POST", r"/api/bank/reconcile-all", lambda c, m, b, s: bank.reconcile_all(c, s.today)),
    ("POST", r"/api/bank/create-all", lambda c, m, b, s: bank.create_all(c, s.today)),
    ("DELETE", r"/api/bank/imports/(\d+)", _do(bank.delete_import)),
    # reajustes
    ("DELETE", r"/api/amount-changes/(\d+)", _do(finance.delete_amount_change)),
    # cartões
    ("GET", r"/api/cards", lambda c, m, b, s: cards.list_cards(c, s.today)),
    ("POST", r"/api/cards", _create(cards.save_card)),
    ("PUT", r"/api/cards/(\d+)", _update(cards.save_card)),
    ("DELETE", r"/api/cards/(\d+)", _do(cards.delete_card)),
    ("GET", r"/api/cards/(\d+)/statement/(\d{4}-\d{2})",
     lambda c, m, b, s: cards.statement(c, int(m.group(1)), m.group(2), s.today)),
    ("POST", r"/api/cards/(\d+)/payments", lambda c, m, b, s: {"id": cards.save_payment(c, {**b, "card_id": int(m.group(1))}, s.today)}),
    ("PUT", r"/api/invoice-payments/(\d+)", lambda c, m, b, s: (cards.save_payment(c, b, s.today, int(m.group(1))), ok())[1]),
    ("DELETE", r"/api/invoice-payments/(\d+)", _do(cards.delete_payment)),
    # lançamentos
    ("POST", r"/api/transactions", lambda c, m, b, s: _create_tx(c, b)),
    ("PUT", r"/api/transactions/(\d+)", _update(finance.save_transaction)),
    ("DELETE", r"/api/transactions/(\d+)", _do(finance.delete_transaction)),
    # fixas
    ("POST", r"/api/fixed", _create(finance.save_fixed)),
    ("PUT", r"/api/fixed/(\d+)", _update(finance.save_fixed)),
    ("DELETE", r"/api/fixed/(\d+)", _do(finance.delete_fixed)),
    # parcelas
    ("POST", r"/api/installments", _create(finance.save_installment)),
    ("PUT", r"/api/installments/(\d+)", _update(finance.save_installment)),
    ("DELETE", r"/api/installments/(\d+)", _do(finance.delete_installment)),
    # ações / FIIs
    ("POST", r"/api/ops", _create(invest.save_op)),
    ("PUT", r"/api/ops/(\d+)", _update(invest.save_op)),
    ("DELETE", r"/api/ops/(\d+)", _do(invest.delete_op)),
    ("POST", r"/api/dividends", _create(invest.save_dividend)),
    ("PUT", r"/api/dividends/(\d+)", _update(invest.save_dividend)),
    ("DELETE", r"/api/dividends/(\d+)", _do(invest.delete_dividend)),
    ("GET", r"/api/assets/(\d+)/history", lambda c, m, b, s: invest.asset_history(c, int(m.group(1)))),
    ("PUT", r"/api/assets/(\d+)/price", lambda c, m, b, s: (invest.set_price(c, int(m.group(1)), b), ok())[1]),
    ("DELETE", r"/api/assets/(\d+)", _do(invest.delete_asset)),
    # renda fixa
    ("POST", r"/api/fi", _create(invest.save_fi)),
    ("PUT", r"/api/fi/(\d+)", _update(invest.save_fi)),
    ("DELETE", r"/api/fi/(\d+)", _do(invest.delete_fi)),
    ("POST", r"/api/fi/moves", _create(invest.save_fi_move)),
    ("PUT", r"/api/fi/moves/(\d+)", _update(invest.save_fi_move)),
    ("DELETE", r"/api/fi/moves/(\d+)", _do(invest.delete_fi_move)),
    ("GET", r"/api/fi/(\d+)/history", lambda c, m, b, s: invest.fi_history(c, int(m.group(1)))),
]
ROUTES = [(method, re.compile(pattern + r"$"), fn) for method, pattern, fn in ROUTES]
