"""Pagamento parcial ou adiantado da fatura do cartão de crédito.
Rodar com:  python -m unittest discover -s tests -v"""
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "site" / "py") if (HERE / "site" / "py").exists() else str(HERE))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from financas import analysis, bank, cards, db, finance  # noqa: E402
from test_bank import b64, make_ofx  # noqa: E402

TODAY = "2026-10-27"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.init(self.conn)
        finance.save_transaction(self.conn, {"date": "2026-10-01", "kind": "entrada", "amount": 5000, "description": "saldo", "category": ""})
        # fecha dia 5, vence dia 12: compras de 6/10 a 5/11 vencem em 12/11
        self.card = cards.save_card(self.conn, {"name": "Visa", "closing_day": 5, "due_day": 12})
        self.buy("2026-10-10", 600, "Mercado", "Mercado")
        self.buy("2026-10-20", 400, "Roupa", "Vestuário")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def buy(self, date, amount, desc, cat):
        return finance.save_transaction(self.conn, {"date": date, "kind": "saida", "amount": amount, "description": desc,
                                                     "category": cat, "card_id": self.card})

    def pay(self, amount, date="2026-10-26", ym="2026-11", **extra):
        return cards.save_payment(self.conn, {"card_id": self.card, "ym": ym, "date": date, "amount": amount, **extra}, TODAY)

    def view(self, ym):
        return finance.month_view(self.conn, ym, TODAY)

    def fatura(self, ym="2026-11"):
        return next(i for i in self.view(ym)["items"] if i["source"] == "fatura")

    def pagamentos(self, ym):
        return [i for i in self.view(ym)["items"] if i["source"] == "pag_fatura"]


class TestPagamentoParcial(Base):
    def test_sem_pagamento_fatura_integral(self):
        f = self.fatura()
        self.assertEqual((f["amount"], f.get("prepaid_total")), (1000.0, None))
        self.assertEqual(self.view("2026-10")["summary"]["saldo_final"], 5000.0)

    def test_parcial_adiantado_sai_do_caixa_na_data_e_abate_a_fatura(self):
        previsto = self.view("2026-11")["summary"]["saldo_previsto"]
        self.pay(300)
        self.assertEqual(self.view("2026-10")["summary"]["saldo_final"], 4700.0)  # saiu em outubro
        f = self.fatura()
        self.assertEqual((f["amount"], f["original_amount"], f["prepaid_total"]), (700.0, 1000.0, 300.0))
        self.assertFalse(f.get("fully_prepaid"))
        self.assertEqual(self.view("2026-11")["summary"]["saldo_previsto"], previsto)  # o total que sai do caixa não muda
        self.assertEqual(self.view("2026-11")["summary"]["a_pagar"], 700.0)

    def test_pagamento_aparece_como_saida_paga_com_a_fatura_de_origem(self):
        pid = self.pay(300)
        (p,) = self.pagamentos("2026-10")
        self.assertEqual((p["ref_id"], p["amount"], p["kind"], p["paid"], p["invoice_ym"]), (pid, 300.0, "saida", True, "2026-11"))
        self.assertIn("Visa", p["description"])

    def test_total_adiantado_quita_a_fatura(self):
        self.pay(1000)
        f = self.fatura()
        self.assertEqual((f["amount"], f["paid"], f["pending"], f.get("fully_prepaid")), (0.0, True, False, True))
        self.assertEqual(self.view("2026-11")["summary"]["a_pagar"], 0.0)
        self.assertEqual(self.view("2026-11")["summary"]["saldo_final"], 4000.0)
        with self.assertRaises(ValueError):
            finance.set_payment(self.conn, {"source": "fatura", "ref_id": self.card, "ym": "2026-11", "paid": False}, TODAY)

    def test_varios_pagamentos_somam(self):
        self.pay(300)
        self.pay(200, date="2026-11-02")
        self.assertEqual(self.fatura()["amount"], 500.0)
        self.assertEqual(self.fatura()["prepaid_total"], 500.0)
        self.assertEqual(self.view("2026-11")["summary"]["saldo_previsto"], 4000.0)

    def test_nao_deixa_pagar_mais_que_o_restante(self):
        self.pay(300)
        with self.assertRaisesRegex(ValueError, "passa do que falta"):
            self.pay(800)
        self.pay(700)
        self.assertTrue(self.fatura()["fully_prepaid"])

    def test_valida_dados(self):
        for amount in (0, -5, ""):
            with self.assertRaises(ValueError):
                self.pay(amount)
        with self.assertRaises(ValueError):
            self.pay(100, date="2026-13-01")
        with self.assertRaisesRegex(ValueError, "não tem compras"):
            self.pay(10, ym="2027-03")
        with self.assertRaises(ValueError):
            cards.save_payment(self.conn, {"card_id": 999, "ym": "2026-11", "date": "2026-10-26", "amount": 10}, TODAY)

    def test_editar_e_excluir(self):
        pid = self.pay(300)
        self.pay(100)
        cards.save_payment(self.conn, {"amount": 500, "date": "2026-10-25", "account_id": None}, TODAY, pid)
        self.assertEqual(self.fatura()["amount"], 400.0)
        with self.assertRaises(ValueError):  # 500 + 100 não pode virar 1.001
            cards.save_payment(self.conn, {"amount": 901, "date": "2026-10-25"}, TODAY, pid)
        cards.delete_payment(self.conn, pid)
        self.assertEqual(self.fatura()["amount"], 900.0)
        self.assertEqual(len(self.pagamentos("2026-10")), 1)

    def test_nao_vira_gasto_novo_nas_categorias(self):
        self.pay(300)
        cats = {c["category"]: c["total"] for c in self.view("2026-10")["by_category"]}
        self.assertNotIn("Cartão de crédito", cats)
        self.assertEqual(sum(cats.values()), 0.0)  # nada gasto direto em outubro: as compras contam na fatura (novembro)
        nov = {c["category"]: c["total"] for c in self.view("2026-11")["by_category"]}
        self.assertEqual((nov["Mercado"], nov["Vestuário"]), (600.0, 400.0))

    def test_pagamento_futuro_fica_a_pagar(self):
        self.pay(300, date="2026-11-03")
        (p,) = self.pagamentos("2026-11")
        self.assertTrue(p["pending"])
        self.assertEqual(self.view("2026-11")["summary"]["a_pagar"], 700.0 + 300.0)

    def test_fatura_e_cartao_mostram_o_que_ja_foi_pago(self):
        self.pay(250)
        st = cards.statement(self.conn, self.card, "2026-11", TODAY)
        self.assertEqual((st["total"], st["paid_amount"], st["remaining"], st["paid"], len(st["payments"])), (1000.0, 250.0, 750.0, False, 1))
        s = next(x for x in cards.list_cards(self.conn, TODAY)[0]["statements"] if x["ym"] == "2026-11")
        self.assertEqual((s["paid_amount"], s["remaining"]), (250.0, 750.0))
        self.pay(750)
        self.assertTrue(cards.statement(self.conn, self.card, "2026-11", TODAY)["paid"])

    def test_marcar_fatura_paga_paga_so_o_restante(self):
        self.pay(300)
        finance.set_payment(self.conn, {"source": "fatura", "ref_id": self.card, "ym": "2026-11", "paid": True}, "2026-11-20")
        v = finance.month_view(self.conn, "2026-11", "2026-11-20")
        f = next(i for i in v["items"] if i["source"] == "fatura")
        self.assertEqual((f["amount"], f["paid"]), (700.0, True))
        self.assertEqual(v["summary"]["saldo_final"], 5000.0 - 1000.0)

    def test_excluir_cartao_apaga_os_pagamentos(self):
        self.pay(300)
        for r in self.conn.execute("SELECT id FROM transactions WHERE card_id = ?", (self.card,)).fetchall():
            finance.delete_transaction(self.conn, r[0])
        cards.delete_card(self.conn, self.card)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM invoice_payments").fetchone()[0], 0)

    def test_relatorio_nao_conta_o_pagamento_como_gasto(self):
        self.pay(300)
        rep = analysis.report(self.conn, "2026-10", "2026-11", TODAY)
        self.assertEqual(rep["totals"]["saidas"], 1000.0)  # 300 pagos antes + 700 no vencimento, sem duplicar
        self.assertEqual(sum(c["total"] for c in rep["categories"]), 1000.0)
        self.assertEqual(sorted(t["description"] for t in rep["top"]), ["Mercado", "Roupa"])

    def test_extrato_do_banco_concilia_com_o_pagamento_da_fatura(self):
        pid = self.pay(300)
        raw = make_ofx([("20261026", "-300.00", "P1", "PAGTO FATURA CARTAO")])
        bank.import_ofx(self.conn, {"data": b64(raw), "filename": "e.ofx"})
        row = bank.overview(self.conn, "pendente", TODAY)["rows"][0]
        cands = bank.candidates(self.conn, row["id"], TODAY)
        self.assertIn(("pag_fatura", pid), [(c["source"], c["ref_id"]) for c in cands])
        bank.reconcile(self.conn, row["id"], {"source": "pag_fatura", "ref_id": pid, "ym": "2026-10"}, TODAY)
        self.assertEqual(bank.overview(self.conn, "conciliada", TODAY)["rows"][0]["memo"], "PAGTO FATURA CARTAO")
        self.assertTrue(next(i for i in self.view("2026-10")["items"] if i["source"] == "pag_fatura")["reconciled"])


if __name__ == "__main__":
    unittest.main()
