"""Quitar antes do prazo: lançamento que paga (ou recebe) adiantado um débito já previsto não conta duas vezes.
Rodar com:  python -m unittest discover -s tests -v"""
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "site" / "py") if (HERE / "site" / "py").exists() else str(HERE))

from financas import db, finance  # noqa: E402

TODAY = "2026-10-27"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.init(self.conn)
        self.tx("2026-10-01", "entrada", 1000, "saldo")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def tx(self, date, kind, amount, desc="x", **extra):
        return finance.save_transaction(self.conn, {"date": date, "kind": kind, "amount": amount,
                                                     "description": desc, "category": "", **extra})

    def fixa(self, kind="saida", amount=200, day=10, desc="Conta A"):
        return finance.save_fixed(self.conn, {"description": desc, "kind": kind, "amount": amount, "day": day,
                                              "start_month": "2026-11", "category": ""})

    def summary(self, ym, today=TODAY):
        return finance.month_view(self.conn, ym, today)["summary"]

    def item(self, ym, source, ref_id, today=TODAY):
        items = finance.month_view(self.conn, ym, today)["items"]
        return next(i for i in items if i["source"] == source and i["ref_id"] == ref_id)

    def target(self, fid, ym="2026-11", source="fixa"):
        return {"source": source, "ref_id": fid, "ym": ym}


class TestQuitarAntes(Base):
    def test_sem_vinculo_conta_em_dobro(self):  # o problema original
        self.fixa()
        self.tx("2026-10-26", "saida", 200, "Conta A paga antes")
        self.assertEqual(self.summary("2026-11")["saldo_previsto"], 600.0)

    def test_vinculo_evita_dobra(self):
        fid = self.fixa()
        t = self.tx("2026-10-26", "saida", 200, "Conta A paga antes")
        finance.add_link(self.conn, {"tx_id": t, **self.target(fid)})
        self.assertEqual(self.summary("2026-10")["saldo_final"], 800.0)
        self.assertEqual(self.summary("2026-11")["saldo_previsto"], 800.0)
        self.assertEqual(self.summary("2026-11")["a_pagar"], 0.0)
        it = self.item("2026-11", "fixa", fid)
        self.assertEqual((it["amount"], it["original_amount"], it["paid"], it["pending"]), (0.0, 200.0, True, False))
        self.assertEqual(it["linked"]["tx_id"], t)
        self.assertEqual(self.item("2026-10", "lancamento", t)["settles"]["ym"], "2026-11")

    def test_confirmar_em_novembro_nao_debita_de_novo(self):
        fid = self.fixa()
        t = self.tx("2026-10-26", "saida", 200)
        finance.add_link(self.conn, {"tx_id": t, **self.target(fid)})
        with self.assertRaises(ValueError):  # não dá para marcar como paga um item já quitado
            finance.set_payment(self.conn, {"source": "fixa", "ref_id": fid, "ym": "2026-11", "paid": True}, "2026-11-10")
        self.assertEqual(self.summary("2026-11", "2026-11-10")["saldo_final"], 800.0)

    def test_corrige_quem_ja_marcou_como_paga_em_novembro(self):  # situação real: já havia confirmado o pagamento
        fid = self.fixa()
        finance.set_payment(self.conn, {"source": "fixa", "ref_id": fid, "ym": "2026-11", "paid": True}, TODAY)
        t = self.tx("2026-10-26", "saida", 200)
        self.assertEqual(self.summary("2026-11", "2026-11-10")["saldo_final"], 600.0)  # em dobro
        finance.add_link(self.conn, {"tx_id": t, **self.target(fid)})
        self.assertEqual(self.summary("2026-11", "2026-11-10")["saldo_final"], 800.0)
        finance.remove_link(self.conn, self.item("2026-11", "fixa", fid)["linked"]["id"])
        it = self.item("2026-11", "fixa", fid)  # sem vínculo volta ao automático (a pagar até o dia 10)
        self.assertFalse(it["manual"])

    def test_recebimento_adiantado(self):
        fid = self.fixa(kind="entrada", amount=50)
        t = self.tx("2026-10-26", "entrada", 50, "Adiantamento")
        self.assertEqual(self.summary("2026-11")["saldo_previsto"], 1100.0)  # 1000 + 50 + 50
        finance.add_link(self.conn, {"tx_id": t, **self.target(fid)})
        self.assertEqual(self.summary("2026-11")["saldo_previsto"], 1050.0)
        self.assertEqual(self.summary("2026-11")["a_receber"], 0.0)

    def test_parcela_e_lancamento_futuro(self):
        pid = finance.save_installment(self.conn, {"description": "Sofá", "first_date": "2026-11-05", "n_installments": 3,
                                                   "installment_amount": 100, "category": ""})
        fut = self.tx("2026-11-20", "saida", 70, "Dentista")
        t1 = self.tx("2026-10-26", "saida", 100, "Sofá antecipado")
        t2 = self.tx("2026-10-27", "saida", 70, "Dentista antecipado")
        finance.add_link(self.conn, {"tx_id": t1, **self.target(pid, source="parcela")})
        finance.add_link(self.conn, {"tx_id": t2, **self.target(fut, source="lancamento")})
        # novembro: parcela 1 e dentista quitados (valem 0); só sobra o que já saiu em outubro
        self.assertEqual(self.summary("2026-11")["saldo_previsto"], 1000 - 100 - 70)
        self.assertEqual(self.summary("2026-11")["a_pagar"], 0.0)
        self.assertEqual(self.summary("2026-12")["saldo_previsto"], 1000 - 100 - 70 - 100)  # parcela 2 segue normal

    def test_desvincular_restaura(self):
        fid = self.fixa()
        t = self.tx("2026-10-26", "saida", 200)
        finance.add_link(self.conn, {"tx_id": t, **self.target(fid)})
        finance.set_settlement(self.conn, t, None)
        self.assertEqual(self.summary("2026-11")["saldo_previsto"], 600.0)
        self.assertEqual(self.item("2026-11", "fixa", fid)["amount"], 200.0)

    def test_pelo_formulario_do_lancamento(self):
        fid = self.fixa()
        t = finance.save_transaction(self.conn, {"date": "2026-10-26", "kind": "saida", "amount": 200, "description": "x",
                                                 "category": "", "settles": self.target(fid)})
        self.assertEqual(self.summary("2026-11")["saldo_previsto"], 800.0)
        finance.save_transaction(self.conn, {"date": "2026-10-26", "kind": "saida", "amount": 195, "description": "x",
                                             "category": ""}, t)  # editar sem mexer no vínculo mantém
        self.assertEqual(self.summary("2026-11")["saldo_previsto"], 805.0)
        finance.save_transaction(self.conn, {"date": "2026-10-26", "kind": "saida", "amount": 195, "description": "x",
                                             "category": "", "settles": None}, t)
        self.assertEqual(self.summary("2026-11")["saldo_previsto"], 605.0)

    def test_erro_ao_vincular_desfaz_o_lancamento_novo(self):
        with self.assertRaises(ValueError):
            finance.save_transaction(self.conn, {"date": "2026-10-26", "kind": "saida", "amount": 1, "description": "y",
                                                 "category": "", "settles": self.target(999)})

    def test_validacoes(self):
        fid = self.fixa()
        ent = self.tx("2026-10-26", "entrada", 200, "entrada")
        with self.assertRaises(ValueError):  # tipo diferente
            finance.add_link(self.conn, {"tx_id": ent, **self.target(fid)})
        t = self.tx("2026-10-26", "saida", 200)
        with self.assertRaises(ValueError):  # mês em que o débito não existe (fixa começa em novembro)
            finance.add_link(self.conn, {"tx_id": t, **self.target(fid, ym="2026-10")})
        finance.add_link(self.conn, {"tx_id": t, **self.target(fid)})
        t2 = self.tx("2026-10-27", "saida", 200)
        with self.assertRaises(ValueError):  # já quitado por outro
            finance.add_link(self.conn, {"tx_id": t2, **self.target(fid)})
        cartao = finance.save_transaction  # compra no cartão não quita débito da conta
        import financas.cards as cards
        cid = cards.save_card(self.conn, {"name": "Visa", "closing_day": 5, "due_day": 12})
        tc = self.tx("2026-10-26", "saida", 200, "no cartão", card_id=cid)
        fid2 = self.fixa(desc="Conta B")
        with self.assertRaises(ValueError):
            finance.add_link(self.conn, {"tx_id": tc, **self.target(fid2)})
        with self.assertRaises(ValueError):  # um lançamento não quita a si mesmo
            finance.add_link(self.conn, {"tx_id": t2, "source": "lancamento", "ref_id": t2, "ym": "2026-10"})

    def test_apagar_remove_vinculos(self):
        fid = self.fixa()
        t = self.tx("2026-10-26", "saida", 200)
        finance.add_link(self.conn, {"tx_id": t, **self.target(fid)})
        finance.delete_transaction(self.conn, t)  # lançamento apagado: o previsto volta a contar
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM occurrence_links").fetchone()[0], 0)
        self.assertEqual(self.item("2026-11", "fixa", fid)["amount"], 200.0)
        t = self.tx("2026-10-26", "saida", 200)
        finance.add_link(self.conn, {"tx_id": t, **self.target(fid)})
        finance.delete_fixed(self.conn, fid)  # débito apagado: o lançamento continua, sem vínculo
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM occurrence_links").fetchone()[0], 0)
        self.assertEqual(self.summary("2026-10")["saldo_final"], 800.0)

    def test_editar_para_cartao_ou_outro_tipo_desfaz_vinculo(self):
        fid = self.fixa()
        t = self.tx("2026-10-26", "saida", 200)
        finance.add_link(self.conn, {"tx_id": t, **self.target(fid)})
        finance.save_transaction(self.conn, {"date": "2026-10-26", "kind": "entrada", "amount": 200, "description": "x",
                                             "category": ""}, t)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM occurrence_links").fetchone()[0], 0)


class TestOpcoes(Base):
    def test_opcoes_do_formulario(self):
        a = self.fixa(desc="Conta A", amount=200)
        b = self.fixa(desc="Conta B", amount=90, day=20)
        finance.set_payment(self.conn, {"source": "fixa", "ref_id": b, "ym": "2026-11", "paid": True}, TODAY)  # pago à mão
        opts = finance.link_options(self.conn, "saida", TODAY)
        nov = [(o["description"], o["amount"], o["paid"]) for o in opts if o["ym"] == "2026-11"]
        self.assertEqual(nov, [("Conta A", 200.0, False), ("Conta B", 90.0, True)])
        self.assertEqual(finance.link_options(self.conn, "entrada", TODAY), [])
        t = self.tx("2026-10-26", "saida", 200)
        finance.add_link(self.conn, {"tx_id": t, **self.target(a)})
        again = finance.link_options(self.conn, "saida", TODAY)
        self.assertNotIn(("Conta A", "2026-11"), [(o["description"], o["ym"]) for o in again])  # quitada: some da lista
        self.assertIn(("Conta A", "2026-12"), [(o["description"], o["ym"]) for o in again])    # os outros meses seguem
        mine = finance.link_options(self.conn, "saida", TODAY, t)
        self.assertEqual([o["description"] for o in mine if o["current"]], ["Conta A"])

    def test_opcoes_de_recebimento(self):  # entradas não têm marca de "pago"
        f = self.fixa(kind="entrada", amount=50, desc="Aluguel de sala")
        t = self.tx("2026-10-26", "entrada", 50, "Sala adiantada")
        nov = [o for o in finance.link_options(self.conn, "entrada", TODAY) if o["ym"] == "2026-11"]
        self.assertEqual([(o["description"], o["amount"], o["paid"]) for o in nov], [("Aluguel de sala", 50.0, False)])
        finance.add_link(self.conn, {"tx_id": t, **self.target(f)})
        mine = finance.link_options(self.conn, "entrada", TODAY, t)
        self.assertEqual([o["description"] for o in mine if o["current"]], ["Aluguel de sala"])

    def test_candidatos_com_mesmo_valor_primeiro(self):
        a = self.fixa(amount=200)
        self.tx("2026-10-20", "saida", 55, "mercado")
        certo = self.tx("2026-10-26", "saida", 200, "Conta A")
        self.tx("2026-10-26", "entrada", 200, "entrada igual")  # tipo diferente: fora
        self.tx("2025-01-05", "saida", 200, "muito antigo")      # fora da janela
        res = finance.link_candidates(self.conn, self.target(a), TODAY)
        self.assertEqual(res["candidates"][0]["id"], certo)
        self.assertTrue(res["candidates"][0]["same_amount"])
        self.assertEqual([c["description"] for c in res["candidates"]], ["Conta A", "mercado"])


if __name__ == "__main__":
    unittest.main()
