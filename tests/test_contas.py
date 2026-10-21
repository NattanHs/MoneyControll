"""Testes das contas (corrente, poupança, dinheiro…) e das transferências."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site" / "py"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from financas import accounts, backup, bank, cards, db, finance, invest, reports  # noqa: E402
from test_bank import b64, make_ofx  # noqa: E402

TODAY = "2026-10-19"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "dados" / "financas.db"
        self.path.parent.mkdir()
        self.conn = db.connect(self.path)
        db.init(self.conn)
        self.a = accounts.default_id(self.conn)
        self.b = accounts.save_account(self.conn, {"name": "Poupança", "kind": "poupanca", "opening_balance": 1000})
        self.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('opening_month', '2026-10')")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def tx(self, date, kind, amount, desc="x", acc=None, **extra):
        return finance.save_transaction(self.conn, {"date": date, "kind": kind, "amount": amount, "description": desc,
                                                     "account_id": acc, **extra})

    def sm(self, acc=None):
        return finance.month_view(self.conn, "2026-10", TODAY, acc)["summary"]


class TestContas(Base):
    def test_conta_padrao_e_migracao_do_saldo_inicial(self):
        tmp = tempfile.TemporaryDirectory()
        conn = db.connect(Path(tmp.name) / "x.db")
        conn.executescript(db.SCHEMA)  # estrutura sem migração
        conn.execute("DELETE FROM accounts")
        conn.execute("INSERT INTO settings(key, value) VALUES ('opening_balance', '2500.5')")
        db.init(conn)
        rows = conn.execute("SELECT name, opening_balance FROM accounts").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("Conta principal", 2500.5)])
        self.assertEqual(conn.execute("SELECT value FROM settings WHERE key = 'opening_balance'").fetchone()[0], "0")
        db.init(conn)  # idempotente
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 1)
        conn.close()
        tmp.cleanup()

    def test_saldos_por_conta_e_total(self):
        self.tx("2026-10-05", "entrada", 500, "Salário", self.a)
        self.tx("2026-10-06", "saida", 100, "Mercado", self.a)
        self.tx("2026-10-07", "entrada", 50, "Juros", self.b)
        info = accounts.list_accounts(self.conn, TODAY)
        by = {x["name"]: x["balance"] for x in info["items"]}
        self.assertEqual(by, {"Conta principal": 400.0, "Poupança": 1050.0})
        self.assertEqual(info["total"], 1450.0)
        self.assertEqual(self.sm()["saldo_final"], 1450.0)
        self.assertEqual(self.sm(self.a)["saldo_final"], 400.0)
        self.assertEqual(self.sm(self.b)["saldo_final"], 1050.0)
        self.assertEqual(reports.overview(self.conn, TODAY)["conta"], 1450.0)

    def test_filtro_de_conta_mostra_so_os_itens_dela(self):
        self.tx("2026-10-05", "saida", 10, "A1", self.a)
        self.tx("2026-10-05", "saida", 20, "B1", self.b)
        names = lambda acc: [i["description"] for i in finance.month_view(self.conn, "2026-10", TODAY, acc)["items"]]
        self.assertEqual(sorted(names(None)), ["A1", "B1"])
        self.assertEqual(names(self.a), ["A1"])
        self.assertEqual(names(self.b), ["B1"])

    def test_fixas_parcelas_e_investimentos_na_conta_escolhida(self):
        finance.save_fixed(self.conn, {"kind": "saida", "description": "Plano", "amount": 100, "day": 10, "start_month": "2026-10", "account_id": self.b})
        finance.save_installment(self.conn, {"description": "Geladeira", "installment_amount": 50, "n_installments": 3,
                                             "first_date": "2026-10-03", "account_id": self.b})
        invest.save_op(self.conn, {"ticker": "PETR4", "kind": "acao", "op": "compra", "date": "2026-10-04", "quantity": 10, "price": 10,
                                   "fees": 0, "affects_cash": True, "account_id": self.b})
        self.assertEqual(self.sm(self.a)["saldo_final"], 0)
        self.assertEqual(self.sm(self.b)["saldo_final"], 1000 - 100 - 50 - 100)

    def test_fatura_sai_da_conta_que_paga(self):
        cid = cards.save_card(self.conn, {"name": "Visa", "closing_day": 5, "due_day": 10, "pay_account_id": self.b})
        self.tx("2026-09-20", "saida", 300, "Compra", card_id=cid)
        self.assertEqual(self.sm(self.a)["saldo_final"], 0)
        self.assertEqual(self.sm(self.b)["saldo_final"], 700)
        self.assertEqual(self.sm()["saldo_final"], 700)

    def test_sem_conta_informada_vai_para_a_principal(self):
        t = self.tx("2026-10-05", "saida", 10, "Sem conta")
        self.assertEqual(self.conn.execute("SELECT account_id FROM transactions WHERE id = ?", (t,)).fetchone()[0], self.a)
        self.conn.execute("UPDATE transactions SET account_id = NULL")  # dados antigos
        self.assertEqual(self.sm(self.a)["saidas"], 10)

    def test_validacoes_e_exclusao(self):
        with self.assertRaises(ValueError):
            accounts.save_account(self.conn, {"name": "poupança"})  # nome repetido (sem diferenciar maiúsculas)
        with self.assertRaises(ValueError):
            self.tx("2026-10-05", "saida", 1, "x", 999)
        self.tx("2026-10-05", "saida", 1, "x", self.b)
        with self.assertRaises(ValueError):
            accounts.delete_account(self.conn, self.b)  # tem movimentação
        c = accounts.save_account(self.conn, {"name": "Carteira", "kind": "dinheiro"})
        accounts.delete_account(self.conn, c)
        self.conn.execute("DELETE FROM transactions")
        accounts.delete_account(self.conn, self.b)
        with self.assertRaises(ValueError):
            accounts.delete_account(self.conn, self.a)  # última conta


class TestTransferencias(Base):
    def test_transferencia_nao_muda_o_total(self):
        self.tx("2026-10-05", "entrada", 500, "Salário", self.a)
        before = self.sm()["saldo_final"]
        accounts.save_transfer(self.conn, {"date": "2026-10-10", "from_account": self.a, "to_account": self.b, "amount": 200})
        self.assertEqual(self.sm()["saldo_final"], before)
        self.assertEqual((self.sm()["entradas"], self.sm()["saidas"]), (500, 0))  # não é receita nem despesa
        self.assertEqual(self.sm(self.a)["saldo_final"], 300)
        self.assertEqual(self.sm(self.b)["saldo_final"], 1200)
        self.assertEqual((self.sm(self.a)["saidas"], self.sm(self.b)["entradas"]), (200, 200))

    def test_linha_neutra_no_total_e_pernas_por_conta(self):
        accounts.save_transfer(self.conn, {"date": "2026-10-10", "from_account": self.a, "to_account": self.b, "amount": 200, "description": "Reserva"})
        rows = finance.month_view(self.conn, "2026-10", TODAY)["items"]
        self.assertEqual([(i["kind"], i["source"]) for i in rows], [("transfer", "transferencia")])
        self.assertIn("Reserva", rows[0]["description"])
        legs = lambda acc: [(i["source"], i["kind"]) for i in finance.month_view(self.conn, "2026-10", TODAY, acc)["items"]]
        self.assertEqual(legs(self.a), [("transf_saida", "saida")])
        self.assertEqual(legs(self.b), [("transf_entrada", "entrada")])

    def test_linhas_de_transferencia_trazem_dados_para_edicao(self):
        tid = accounts.save_transfer(self.conn, {"date": "2026-10-10", "from_account": self.a, "to_account": self.b, "amount": 200, "description": "Reserva"})
        for acc in (None, self.a, self.b):
            row = finance.month_view(self.conn, "2026-10", TODAY, acc)["items"][0]
            self.assertEqual((row["ref_id"], row["from_account"], row["to_account"], row["note"], row["amount"]), (tid, self.a, self.b, "Reserva", 200))

    def test_transferencia_fica_fora_de_categorias_e_orcamento(self):
        accounts.save_transfer(self.conn, {"date": "2026-10-10", "from_account": self.a, "to_account": self.b, "amount": 200})
        self.assertEqual(finance.month_view(self.conn, "2026-10", TODAY, self.a)["by_category"], [])

    def test_validacoes(self):
        for bad in ({"from_account": self.a, "to_account": self.a}, {"from_account": self.a, "to_account": 99},
                    {"from_account": self.a, "to_account": self.b, "amount": 0}):
            with self.assertRaises(ValueError):
                accounts.save_transfer(self.conn, {"date": "2026-10-10", "amount": 10, **bad})
        t = accounts.save_transfer(self.conn, {"date": "2026-10-10", "from_account": self.a, "to_account": self.b, "amount": 10})
        accounts.save_transfer(self.conn, {"date": "2026-10-11", "from_account": self.b, "to_account": self.a, "amount": 15}, t)
        self.assertEqual(self.sm(self.a)["saldo_final"], 15)
        accounts.delete_transfer(self.conn, t)
        self.assertEqual(self.sm(self.a)["saldo_final"], 0)


class TestConciliacaoPorConta(Base):
    def imp(self, raw, **kw):
        return bank.import_ofx(self.conn, {"data": b64(raw), "filename": "e.ofx", **kw})

    def rows(self, view="pendente"):
        return bank.overview(self.conn, view, TODAY)["rows"]

    def test_extrato_so_concilia_com_lancamentos_da_conta_escolhida(self):
        self.tx("2026-10-10", "saida", 100, "Farmácia", self.a)
        self.imp(make_ofx([("20261010", "-100.00", "1", "FARMACIA")], acct="B-1"), account_id=self.b)
        self.assertIsNone(self.rows()[0]["suggestion"])  # o lançamento é da outra conta
        self.imp(make_ofx([("20261010", "-100.00", "2", "FARMACIA")], acct="A-1"), account_id=self.a)
        sug = [r for r in self.rows() if r["account_id"] == self.a][0]["suggestion"]
        self.assertEqual(sug["description"], "Farmácia")

    def test_lancar_novo_cai_na_conta_do_extrato(self):
        self.imp(make_ofx([("20261010", "-33.00", "1", "LOJA")], acct="B-1"), account_id=self.b)
        r = self.rows()[0]
        bank.create_from(self.conn, r["id"], {}, TODAY)
        self.assertEqual(self.conn.execute("SELECT account_id FROM transactions").fetchone()[0], self.b)

    def test_preview_lembra_a_conta_do_extrato(self):
        raw = make_ofx([("20261010", "-1.00", "1", "X")], acct="B-1")
        self.imp(raw, account_id=self.b)
        pv = bank.preview(self.conn, {"data": b64(make_ofx([("20261011", "-1.00", "2", "Y")], acct="B-1"))})
        self.assertEqual(pv["account_id"], self.b)
        self.assertEqual(len(pv["accounts"]), 2)

    def test_conferencia_de_saldo_por_conta(self):
        self.tx("2026-10-05", "entrada", 500, "Freela", self.a)
        self.imp(make_ofx([("20261005", "500.00", "1", "TED")], acct="A-1", ledger=("20261018", "500.00")), account_id=self.a)
        self.imp(make_ofx([("20261006", "10.00", "2", "JUROS")], acct="B-1", ledger=("20261018", "1000.00")), account_id=self.b)
        checks = {c["account_id"]: c for c in bank.overview(self.conn, "pendente", TODAY)["balance_checks"]}
        self.assertEqual((checks[self.a]["app"], checks[self.a]["diff"]), (500.0, 0.0))
        self.assertEqual((checks[self.b]["app"], checks[self.b]["diff"]), (1000.0, 0.0))

    def test_transferencia_concilia_nos_dois_extratos(self):
        t = accounts.save_transfer(self.conn, {"date": "2026-10-10", "from_account": self.a, "to_account": self.b, "amount": 200})
        self.imp(make_ofx([("20261010", "-200.00", "1", "TED PARA POUPANCA")], acct="A-1"), account_id=self.a)
        self.imp(make_ofx([("20261010", "200.00", "2", "TED RECEBIDA")], acct="B-1"), account_id=self.b)
        for r in self.rows():
            self.assertTrue(r["suggestion"], r["memo"])
            bank.reconcile(self.conn, r["id"], r["suggestion"], TODAY)  # cada perna tem chave própria
        self.assertEqual(len(self.rows("conciliada")), 2)
        self.assertEqual(self.sm()["saldo_final"], 1000)  # nada muda no total


class TestReset(Base):
    def test_reset_deixa_uma_conta_padrao(self):
        self.tx("2026-10-05", "saida", 1, "x", self.b)
        backup.reset(self.conn, self.path, {"confirm": "RESETAR", "wipe_backups": True})
        rows = self.conn.execute("SELECT name, opening_balance FROM accounts").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("Conta principal", 0.0)])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM transfers").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
