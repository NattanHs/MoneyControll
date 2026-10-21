"""Testes das melhorias: reajuste sem reescrever o passado, pago/a pagar, cartão, orçamento,
edição de investimentos, IR estimado e backups."""
import base64
import sqlite3
from datetime import datetime
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site" / "py"))

from financas import backup, cards, db, finance, invest, reports  # noqa: E402

TODAY = "2026-06-15"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "dados" / "financas.db"
        self.path.parent.mkdir()
        self.conn = db.connect(self.path)
        db.init(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def tx(self, date, kind, amount, desc="x", cat="", **extra):
        return finance.save_transaction(self.conn, {"date": date, "kind": kind, "amount": amount,
                                                     "description": desc, "category": cat, **extra})

    def view(self, ym, today=TODAY):
        return finance.month_view(self.conn, ym, today)

    def summary(self, ym, today=TODAY):
        return self.view(ym, today)["summary"]


class TestReajuste(Base):
    def setUp(self):
        super().setUp()
        self.fid = finance.save_fixed(self.conn, {"kind": "saida", "description": "Aluguel", "amount": 1000,
                                                  "day": 5, "start_month": "2026-01"})

    def amount(self, ym):
        items = finance.items_for_month(finance.load_all(self.conn), ym, TODAY)
        return next(i["amount"] for i in items if i["source"] == "fixa")

    def edit(self, **kw):
        d = {"kind": "saida", "description": "Aluguel", "day": 5, "start_month": "2026-01"}
        d.update(kw)
        finance.save_fixed(self.conn, d, self.fid)

    def test_reajuste_nao_muda_o_passado(self):
        self.edit(amount=1200, amount_from="2026-07")
        self.assertEqual(self.amount("2026-06"), 1000)
        self.assertEqual(self.amount("2026-07"), 1200)
        self.assertEqual(self.amount("2027-03"), 1200)
        self.assertEqual(self.summary("2026-03")["saidas"], 1000)

    def test_segundo_reajuste_e_correcao(self):
        self.edit(amount=1200, amount_from="2026-07")
        self.edit(amount=1500, amount_from="2026-10")
        self.assertEqual([self.amount(m) for m in ("2026-08", "2026-10", "2026-12")], [1200, 1500, 1500])
        self.edit(amount=1250, amount_from="2026-10")  # corrige o mesmo mês
        self.assertEqual(self.amount("2026-11"), 1250)

    def test_editar_descricao_sem_mes_nao_altera_valor(self):
        self.edit(amount=1200, amount_from="2026-07")
        self.edit(description="Aluguel apto", amount=1200)  # sem amount_from
        self.assertEqual(self.amount("2026-02"), 1000)
        self.assertEqual(self.amount("2026-09"), 1200)

    def test_mesmo_valor_nao_cria_reajuste(self):
        self.edit(amount=1000, amount_from="2026-07")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM amount_changes").fetchone()[0], 0)

    def test_aplicar_desde_o_inicio_corrige_tudo(self):
        self.edit(amount=900, amount_from="2026-01")
        self.assertEqual(self.amount("2026-01"), 900)

    def test_list_fixed_mostra_valor_atual_e_remover_reajuste(self):
        self.edit(amount=1200, amount_from="2026-07")
        row = finance.list_fixed(self.conn, "2026-06")[0]
        self.assertEqual((row["amount"], row["base_amount"]), (1000, 1000))
        row = finance.list_fixed(self.conn, "2026-08")[0]
        self.assertEqual(row["amount"], 1200)
        finance.delete_amount_change(self.conn, row["changes"][0]["id"])
        self.assertEqual(self.amount("2026-08"), 1000)

    def test_parcela_com_reajuste(self):
        iid = finance.save_installment(self.conn, {"description": "TV", "installment_amount": 100,
                                                   "first_date": "2026-01-10", "n_installments": 6})
        finance.save_installment(self.conn, {"description": "TV", "first_date": "2026-01-10",
                                             "n_installments": 6, "installment_amount": 150,
                                             "amount_from": "2026-04"}, iid)
        data = finance.load_all(self.conn)
        val = lambda ym: next(i["amount"] for i in finance.items_for_month(data, ym, TODAY) if i["source"] == "parcela")
        self.assertEqual([val("2026-01"), val("2026-03"), val("2026-04"), val("2026-06")], [100, 100, 150, 150])
        row = finance.list_installments(self.conn, "2026-02")[0]
        self.assertEqual(row["total"], 3 * 100 + 3 * 150)
        self.assertEqual(row["remaining_amount"], 100 + 150 * 3)  # mar a 100; abr, mai e jun a 150

    def test_excluir_remove_reajustes(self):
        self.edit(amount=1200, amount_from="2026-07")
        finance.delete_fixed(self.conn, self.fid)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM amount_changes").fetchone()[0], 0)


class TestPago(Base):
    def test_saida_futura_fica_a_pagar_e_nao_entra_no_saldo(self):
        self.tx("2026-06-01", "entrada", 5000)
        self.tx("2026-06-10", "saida", 800, "Luz")
        self.tx("2026-06-25", "saida", 300, "Internet")
        s = self.summary("2026-06")
        self.assertEqual((s["saidas"], s["a_pagar"], s["saldo_final"], s["saldo_previsto"]), (800, 300, 4200, 3900))

    def test_marcar_como_paga_e_voltar(self):
        self.tx("2026-06-01", "entrada", 5000)
        tid = self.tx("2026-06-25", "saida", 300)
        finance.set_payment(self.conn, {"source": "lancamento", "ref_id": tid, "ym": "2026-06", "paid": True}, TODAY)
        self.assertEqual(self.summary("2026-06")["saldo_final"], 4700)
        finance.set_payment(self.conn, {"source": "lancamento", "ref_id": tid, "ym": "2026-06", "paid": False}, TODAY)
        self.assertEqual(self.summary("2026-06")["saldo_final"], 5000)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM payment_marks").fetchone()[0], 0)

    def test_conta_atrasada_nao_paga(self):
        self.tx("2026-06-01", "entrada", 5000)
        tid = self.tx("2026-06-05", "saida", 400)
        finance.set_payment(self.conn, {"source": "lancamento", "ref_id": tid, "ym": "2026-06", "paid": False}, TODAY)
        s = self.summary("2026-06")
        self.assertEqual((s["saldo_final"], s["a_pagar"], s["saldo_previsto"]), (5000, 400, 4600))

    def test_fixa_do_mes_atual_e_marca_por_mes(self):
        fid = finance.save_fixed(self.conn, {"kind": "saida", "description": "Aluguel", "amount": 1000, "day": 20,
                                             "start_month": "2026-05"})
        self.assertEqual(self.summary("2026-06")["a_pagar"], 1000)
        self.assertEqual(self.summary("2026-05")["a_pagar"], 0)
        finance.set_payment(self.conn, {"source": "fixa", "ref_id": fid, "ym": "2026-06", "paid": True}, TODAY)
        s = self.summary("2026-06")
        self.assertEqual((s["saidas"], s["a_pagar"]), (1000, 0))
        self.assertEqual(self.summary("2026-07")["a_pagar"], 1000)  # outro mês não é afetado

    def test_saldo_so_carrega_o_que_foi_pago(self):
        self.tx("2026-05-01", "entrada", 1000)
        self.tx("2026-05-28", "saida", 200)  # vence depois de "hoje" (2026-05-20)
        s = self.summary("2026-06", "2026-05-20")
        self.assertEqual(s["saldo_anterior"], 1000)
        self.assertEqual(s["saldo_previsto"], 800)

    def test_pagar_inexistente_ou_entrada(self):
        eid = self.tx("2026-06-01", "entrada", 10)
        with self.assertRaises(ValueError):
            finance.set_payment(self.conn, {"source": "lancamento", "ref_id": eid, "ym": "2026-06", "paid": True}, TODAY)
        with self.assertRaises(ValueError):
            finance.set_payment(self.conn, {"source": "fixa", "ref_id": 99, "ym": "2026-06", "paid": True}, TODAY)


class TestCartao(Base):
    def setUp(self):
        super().setUp()
        # fecha dia 10, vence dia 17 (mesmo mês do fechamento)
        self.cid = cards.save_card(self.conn, {"name": "Nubank", "closing_day": 10, "due_day": 17})

    def test_regra_de_fechamento(self):
        card = {"closing_day": 10, "due_day": 17}
        self.assertEqual(cards.due_ym(card, "2026-06-10"), "2026-06")
        self.assertEqual(cards.due_ym(card, "2026-06-11"), "2026-07")
        card = {"closing_day": 25, "due_day": 5}  # vence no mês seguinte ao fechamento
        self.assertEqual(cards.due_ym(card, "2026-06-25"), "2026-07")
        self.assertEqual(cards.due_ym(card, "2026-06-26"), "2026-08")

    def test_compra_vira_fatura_no_vencimento(self):
        self.tx("2026-05-12", "saida", 200, "Mercado", "Mercado", card_id=self.cid)
        self.tx("2026-06-03", "saida", 100, "Farmácia", "Saúde", card_id=self.cid)
        items = self.view("2026-06")["items"]
        self.assertEqual([i["description"] for i in items], ["Fatura Nubank"])
        self.assertEqual(items[0]["amount"], 300)
        self.assertEqual(items[0]["date"], "2026-06-17")
        self.assertEqual(self.summary("2026-05")["saidas"] + self.summary("2026-05")["a_pagar"], 0)

    def test_fatura_futura_a_pagar_e_paga_depois(self):
        self.tx("2026-06-03", "saida", 100, "Farmácia", card_id=self.cid)
        self.assertEqual(self.summary("2026-06")["a_pagar"], 100)  # vence 17, hoje é 15
        self.assertEqual(self.summary("2026-06", "2026-06-17")["saidas"], 100)
        finance.set_payment(self.conn, {"source": "fatura", "ref_id": self.cid, "ym": "2026-06", "paid": True}, TODAY)
        self.assertEqual(self.summary("2026-06")["saidas"], 100)

    def test_categorias_da_fatura(self):
        self.tx("2026-06-02", "saida", 100, "A", "Saúde", card_id=self.cid)
        self.tx("2026-06-03", "saida", 50, "B", "Lazer", card_id=self.cid)
        self.assertEqual({c["category"]: c["total"] for c in self.view("2026-06")["by_category"]},
                         {"Saúde": 100, "Lazer": 50})

    def test_parcelas_no_cartao(self):
        finance.save_installment(self.conn, {"description": "TV", "installment_amount": 100, "first_date": "2026-05-20",
                                             "n_installments": 3, "card_id": self.cid})
        # compra em 20/05 (após fechamento dia 10) -> 1ª parcela na fatura de jun... 20/05 -> fecha jun -> vence jun/17
        ym = [m for m in ("2026-05", "2026-06", "2026-07", "2026-08", "2026-09")
              if any(i["source"] == "fatura" for i in self.view(m)["items"])]
        self.assertEqual(ym, ["2026-06", "2026-07", "2026-08"])
        self.assertEqual(self.summary("2026-07")["a_pagar"], 100)

    def test_excluir_cartao_em_uso_e_validacoes(self):
        self.tx("2026-06-03", "saida", 100, card_id=self.cid)
        with self.assertRaises(ValueError):
            cards.delete_card(self.conn, self.cid)
        with self.assertRaises(ValueError):
            self.tx("2026-06-03", "saida", 100, card_id=999)
        eid = self.tx("2026-06-03", "entrada", 100, card_id=self.cid)  # cartão ignorado em entrada
        self.assertIsNone(self.conn.execute("SELECT card_id FROM transactions WHERE id=?", (eid,)).fetchone()[0])
        tid = self.tx("2026-06-04", "saida", 5, card_id=self.cid)
        with self.assertRaises(ValueError):
            finance.set_payment(self.conn, {"source": "lancamento", "ref_id": tid, "ym": "2026-06", "paid": True}, TODAY)

    def test_extrato_da_fatura(self):
        self.tx("2026-06-02", "saida", 100, "A", card_id=self.cid)
        st = cards.statement(self.conn, self.cid, "2026-06", TODAY)
        self.assertEqual((st["total"], st["closing_date"], st["due_date"], st["paid"]), (100, "2026-06-10", "2026-06-17", False))
        self.assertEqual(len(st["lines"]), 1)


class TestOrcamentoERepeticao(Base):
    def test_orcamento_alerta(self):
        finance.save_budget(self.conn, {"category": "Mercado", "amount": 500})
        self.tx("2026-06-02", "saida", 420, "x", "mercado")
        b = self.view("2026-06")["budgets"][0]
        self.assertEqual((b["spent"], b["pct"], b["state"]), (420, 84.0, "warn"))
        self.tx("2026-06-03", "saida", 100, "y", "Mercado")
        self.assertEqual(self.view("2026-06")["budgets"][0]["state"], "over")
        self.assertEqual(self.view("2026-07")["budgets"][0]["state"], "ok")

    def test_orcamento_nao_duplica_categoria(self):
        finance.save_budget(self.conn, {"category": "Lazer", "amount": 100})
        finance.save_budget(self.conn, {"category": "lazer", "amount": 150})
        rows = finance.list_budgets(self.conn)
        self.assertEqual((len(rows), rows[0]["amount"]), (1, 150))

    def test_alertas_na_visao_geral(self):
        finance.save_budget(self.conn, {"category": "Lazer", "amount": 100})
        self.tx(TODAY, "saida", 95, "cinema", "Lazer")
        o = reports.overview(self.conn, TODAY)
        self.assertEqual(o["budget_alerts"][0]["category"], "Lazer")

    def test_repetir_lancamento(self):
        self.tx("2026-01-31", "saida", 50, "Academia", repeat=3)
        dates = sorted(r[0] for r in self.conn.execute("SELECT date FROM transactions"))
        self.assertEqual(dates, ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30"])

    def test_sugestoes(self):
        self.tx("2026-01-10", "saida", 50, "Mercado", "Mercado")
        self.tx("2026-02-10", "saida", 80, "mercado", "Mercado")
        s = finance.suggestions(self.conn)
        self.assertEqual((len(s), s[0]["amount"]), (1, 80))


class TestEdicaoInvest(Base):
    def buy(self, ticker, date, qty, price):
        return invest.save_op(self.conn, {"ticker": ticker, "op": "compra", "date": date, "quantity": qty,
                                          "price": price, "kind": "acao"})

    def pos(self, t):
        return next(p for p in invest.positions(self.conn, TODAY) if p["ticker"] == t)

    def test_editar_compra_recalcula_preco_medio(self):
        a = self.buy("PETR4", "2026-01-10", 100, 30)
        self.buy("PETR4", "2026-02-10", 100, 40)
        self.assertAlmostEqual(self.pos("PETR4")["avg_price"], 35)
        invest.save_op(self.conn, {"ticker": "PETR4", "op": "compra", "date": "2026-01-10", "quantity": 100,
                                   "price": 20}, a)
        self.assertAlmostEqual(self.pos("PETR4")["avg_price"], 30)

    def test_editar_nao_pode_deixar_venda_sem_saldo(self):
        a = self.buy("VALE3", "2026-01-10", 100, 60)
        invest.save_op(self.conn, {"ticker": "VALE3", "op": "venda", "date": "2026-02-10", "quantity": 80, "price": 70})
        with self.assertRaises(ValueError):
            invest.save_op(self.conn, {"ticker": "VALE3", "op": "compra", "date": "2026-01-10", "quantity": 50,
                                       "price": 60}, a)
        self.assertEqual(self.conn.execute("SELECT quantity FROM asset_ops WHERE id=?", (a,)).fetchone()[0], 100)

    def test_trocar_ativo_da_operacao(self):
        a = self.buy("ABEV3", "2026-01-10", 100, 10)
        invest.save_op(self.conn, {"ticker": "BBAS3", "op": "compra", "date": "2026-01-10", "quantity": 100,
                                   "price": 10, "kind": "acao"}, a)
        tickers = [p["ticker"] for p in invest.positions(self.conn, TODAY)]
        self.assertEqual(tickers, ["BBAS3"])  # ABEV3 ficou vazio e foi removido

    def test_editar_provento(self):
        self.buy("HGLG11", "2026-01-10", 10, 100)
        did = invest.save_dividend(self.conn, {"ticker": "HGLG11", "date": "2026-02-10", "amount": 10})
        invest.save_dividend(self.conn, {"ticker": "HGLG11", "date": "2026-02-12", "amount": 12, "tipo": "Rendimento"}, did)
        row = self.conn.execute("SELECT * FROM dividends WHERE id=?", (did,)).fetchone()
        self.assertEqual((row["amount"], row["date"], row["tipo"]), (12, "2026-02-12", "Rendimento"))
        with self.assertRaises(ValueError):
            invest.save_dividend(self.conn, {"ticker": "HGLG11", "date": "2026-02-12", "amount": 1}, 999)

    def test_editar_movimento_renda_fixa_valida_saldo(self):
        fi = invest.save_fi(self.conn, {"name": "CDB", "pct_cdi": 100})
        ap = invest.save_fi_move(self.conn, {"fi_id": fi, "kind": "aporte", "date": "2026-01-10", "amount": 1000})
        invest.save_fi_move(self.conn, {"fi_id": fi, "kind": "resgate", "date": "2026-02-10", "amount": 800})
        with self.assertRaises(ValueError):
            invest.save_fi_move(self.conn, {"kind": "aporte", "date": "2026-01-10", "amount": 500}, ap)
        invest.save_fi_move(self.conn, {"kind": "aporte", "date": "2026-01-10", "amount": 1500}, ap)
        self.assertEqual(invest.fi_products(self.conn, TODAY, 13.65)[0]["saldo"], 700)


class TestImposto(unittest.TestCase):
    def test_faixas(self):
        self.assertEqual([invest.ir_rate(d) for d in (0, 180, 181, 360, 361, 720, 721)],
                         [0.225, 0.225, 0.20, 0.20, 0.175, 0.175, 0.15])
        self.assertEqual((invest.iof_rate(1), invest.iof_rate(29), invest.iof_rate(30)), (0.96, 0.03, 0.0))

    def test_ir_por_aporte(self):
        moves = [{"id": 1, "date": "2025-01-10", "kind": "aporte", "amount": 10000.0},
                 {"id": 2, "date": "2026-06-10", "kind": "aporte", "amount": 5000.0}]
        t = invest.fi_tax(moves, "2026-10-19", 16500.0)
        self.assertEqual(t["yield"], 1500.0)
        self.assertAlmostEqual(t["ir"], 269.39, places=2)
        self.assertEqual(t["iof"], 0.0)

    def test_isento_e_sem_rendimento(self):
        moves = [{"id": 1, "date": "2025-01-10", "kind": "aporte", "amount": 1000.0}]
        self.assertEqual(invest.fi_tax(moves, "2026-10-19", 1200.0, "isento")["total"], 0)
        self.assertEqual(invest.fi_tax(moves, "2026-10-19", 1000.0)["total"], 0)

    def test_iof_em_resgate_antes_de_30_dias(self):
        moves = [{"id": 1, "date": "2026-10-10", "kind": "aporte", "amount": 1000.0}]
        t = invest.fi_tax(moves, "2026-10-15", 1010.0)  # 5 dias: IOF 83% de 10 = 8,30; IR 22,5% de 1,70
        self.assertAlmostEqual(t["iof"], 8.30, places=2)
        self.assertAlmostEqual(t["ir"], 0.38, places=2)

    def test_resgate_consome_o_mais_antigo(self):
        moves = [{"id": 1, "date": "2025-01-10", "kind": "aporte", "amount": 1000.0},
                 {"id": 2, "date": "2026-09-10", "kind": "aporte", "amount": 1000.0},
                 {"id": 3, "date": "2026-10-01", "kind": "resgate", "amount": 1000.0}]
        t = invest.fi_tax(moves, "2026-10-19", 1100.0)  # sobrou só o aporte novo (39 dias): 22,5%
        self.assertAlmostEqual(t["ir"], 100 * 0.225, places=2)

    def test_produtos_e_projecao_trazem_liquido(self):
        conn = db.connect(Path(tempfile.mkdtemp()) / "t.db")
        db.init(conn)
        fi = invest.save_fi(conn, {"name": "CDB", "pct_cdi": 100})
        isento = invest.save_fi(conn, {"name": "LCI", "pct_cdi": 90, "tax": "isento"})
        for f in (fi, isento):
            invest.save_fi_move(conn, {"fi_id": f, "kind": "aporte", "date": "2026-01-10", "amount": 10000})
        p = {x["name"]: x for x in invest.fi_products(conn, "2026-10-19", 13.65)}
        self.assertEqual(p["LCI"]["ir_dezembro"], 0)
        self.assertGreater(p["CDB"]["ir_dezembro"], 0)
        self.assertLess(p["CDB"]["liquido_dezembro"], p["CDB"]["saldo_dezembro"])
        s = invest.projection(conn, "2026-10-19", 13.65)["summary"]
        self.assertAlmostEqual(s["patrimonio_dezembro_liquido"], s["patrimonio_dezembro"] - s["ir_dezembro"], places=2)
        self.assertLessEqual(s["year_total_liquido"], s["year_total"])
        conn.close()


class TestBackup(Base):
    def test_backup_diario_uma_vez_e_limite(self):
        self.assertIsNone(backup.auto_backup(self.path, "2026-06-01"))  # banco vazio: não copia
        self.tx("2026-06-01", "entrada", 10)
        self.conn.commit()
        self.assertIsNotNone(backup.auto_backup(self.path, "2026-06-01"))
        self.assertIsNone(backup.auto_backup(self.path, "2026-06-01"))  # já existe hoje
        for d in range(2, 25):
            backup.auto_backup(self.path, f"2026-06-{d:02d}")
        names = [b["name"] for b in backup.list_backups(self.path) if b["kind"] == "auto"]
        self.assertEqual(len(names), backup.AUTO_KEEP)
        self.assertIn("auto-2026-06-24.db", names)
        self.assertNotIn("auto-2026-06-01.db", names)

    def test_restaurar_e_copia_de_seguranca(self):
        self.tx("2026-06-01", "entrada", 100, "original")
        self.conn.commit()
        path = backup.manual_backup(self.conn, self.path)
        self.tx("2026-06-02", "entrada", 999, "depois")
        self.conn.commit()
        res = backup.restore(self.conn, self.path, backup.backup_path(self.path, path.name))
        self.conn.commit()
        rows = [r[0] for r in self.conn.execute("SELECT description FROM transactions")]
        self.assertEqual(rows, ["original"])
        safety = backup.backup_path(self.path, res["safety"])
        copy = sqlite3.connect(str(safety))
        self.assertEqual(copy.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 2)
        copy.close()

    def test_restaurar_arquivo_enviado_e_invalidos(self):
        self.tx("2026-06-01", "entrada", 100, "original")
        self.conn.commit()
        raw = backup.manual_backup(self.conn, self.path).read_bytes()
        self.tx("2026-06-02", "entrada", 5, "novo")
        self.conn.commit()
        backup.restore_upload(self.conn, self.path, base64.b64encode(raw).decode())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 1)
        for bad in (base64.b64encode(b"nao sou um banco").decode(), "###"):
            with self.assertRaises(ValueError):
                backup.restore_upload(self.conn, self.path, bad)
        other = Path(self.tmp.name) / "outro.db"
        c = sqlite3.connect(str(other))
        c.execute("CREATE TABLE x(a)")
        c.commit()
        c.close()
        with self.assertRaises(ValueError):
            backup.restore_upload(self.conn, self.path, base64.b64encode(other.read_bytes()).decode())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 1)

    def test_nome_de_backup_nao_permite_caminho(self):
        for name in ("../financas.db", "auto-x/../../a.db", "", None, "a.txt"):
            with self.assertRaises(ValueError):
                backup.backup_path(self.path, name)

    def test_migracao_de_banco_antigo(self):
        old = Path(self.tmp.name) / "antigo.db"
        c = sqlite3.connect(str(old))
        c.executescript("""
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, kind TEXT NOT NULL,
              description TEXT NOT NULL, category TEXT NOT NULL DEFAULT '', amount REAL NOT NULL);
            CREATE TABLE installments (id INTEGER PRIMARY KEY AUTOINCREMENT, description TEXT NOT NULL,
              category TEXT NOT NULL DEFAULT '', installment_amount REAL NOT NULL, first_date TEXT NOT NULL,
              n_installments INTEGER NOT NULL);
            CREATE TABLE fixed_income (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, pct_cdi REAL NOT NULL DEFAULT 100);
            INSERT INTO transactions(date, kind, description, amount) VALUES ('2026-01-01', 'saida', 'velho', 10);
            INSERT INTO fixed_income(name, pct_cdi) VALUES ('CDB', 100);
        """)
        c.commit()
        c.close()
        conn = db.connect(old)
        db.init(conn)
        self.assertEqual(conn.execute("SELECT card_id FROM transactions").fetchone()[0], None)
        self.assertEqual(conn.execute("SELECT tax FROM fixed_income").fetchone()[0], "regressivo")
        self.assertEqual(finance.month_view(conn, "2026-01", TODAY)["summary"]["saidas"], 10)
        conn.close()


class TestResetar(Base):
    def popular(self):
        self.tx("2026-06-01", "saida", 10, "x")
        finance.save_fixed(self.conn, {"kind": "saida", "description": "Aluguel", "amount": 100, "day": 5, "start_month": "2026-01"})
        cards.save_card(self.conn, {"name": "Visa", "closing_day": 5, "due_day": 12})
        finance.save_budget(self.conn, {"category": "Mercado", "amount": 500})
        invest.save_op(self.conn, {"ticker": "PETR4", "kind": "acao", "op": "compra", "date": "2026-06-01", "quantity": 10, "price": 30, "fees": 0, "affects_cash": True})
        self.conn.execute("UPDATE accounts SET opening_balance = 999")
        self.conn.commit()

    def contagem(self):
        return sum(self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in
                   ("transactions", "fixed_items", "installments", "credit_cards", "budgets", "assets", "asset_ops", "payment_marks"))

    def test_exige_confirmacao(self):
        self.popular()
        for bad in ({}, {"confirm": "sim"}, {"confirm": ""}):
            with self.assertRaises(ValueError):
                backup.reset(self.conn, self.path, bad)
        self.assertGreater(self.contagem(), 0)

    def test_reseta_tudo_e_guarda_copia_de_seguranca(self):
        self.popular()
        r = backup.reset(self.conn, self.path, {"confirm": "resetar"})
        self.assertEqual(self.contagem(), 0)
        self.assertEqual(self.conn.execute("SELECT SUM(opening_balance), COUNT(*) FROM accounts").fetchone()[:], (0, 1))
        self.assertTrue(r["safety"])
        names = [b["name"] for b in backup.list_backups(self.path)]
        self.assertEqual(names, [r["safety"]])
        # a cópia de segurança guarda os dados antigos e permite voltar
        backup.restore(self.conn, self.path, backup.backup_path(self.path, r["safety"]))
        self.assertGreater(self.contagem(), 0)

    def test_apagar_tambem_os_backups_para_distribuir(self):
        self.popular()
        backup.manual_backup(self.conn, self.path)
        backup.auto_backup(self.path, "2026-06-01")
        self.assertEqual(len(backup.list_backups(self.path)), 2)
        r = backup.reset(self.conn, self.path, {"confirm": "RESETAR", "wipe_backups": True})
        self.assertEqual((r["safety"], r["backups_removed"]), (None, 2))
        self.assertEqual(backup.list_backups(self.path), [])
        self.assertEqual(self.contagem(), 0)
        self.assertNotIn(b"PETR4", self.path.read_bytes())  # nada do que foi apagado sobra no arquivo
        # continua funcionando do zero
        self.tx("2026-06-02", "entrada", 5, "novo")
        self.assertEqual(self.summary("2026-06")["entradas"], 5)

    def test_reseta_tambem_conciliacao(self):
        from financas import bank
        self.popular()
        raw = ("<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKACCTFROM><BANKID>1<ACCTID>2</BANKACCTFROM><BANKTRANLIST>"
               "<STMTTRN><DTPOSTED>20260601<TRNAMT>-10.00<FITID>1<MEMO>X</STMTTRN></BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>")
        import base64
        bank.import_ofx(self.conn, {"data": base64.b64encode(raw.encode()).decode(), "filename": "a.ofx"})
        backup.reset(self.conn, self.path, {"confirm": "RESETAR"})
        for t in ("bank_imports", "bank_txns", "bank_links"):
            self.assertEqual(self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()


class TestBackupExterno(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dados = Path(self.tmp.name) / "dados"
        self.dados.mkdir()
        self.db_path = self.dados / "financas.db"
        self.conn = db.connect(self.db_path)
        db.init(self.conn)
        self.ext = Path(self.tmp.name) / "nuvem" / "backups"

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def add_data(self):
        finance.save_transaction(self.conn, {"date": "2026-10-05", "kind": "entrada", "amount": 10, "description": "x"})
        self.conn.commit()

    def test_sem_configuracao_pede_para_configurar_so_quando_ha_dados(self):
        self.assertFalse(backup.external_status(self.db_path)["needs_setup"])
        self.add_data()
        st = backup.external_status(self.db_path)
        self.assertTrue(st["needs_setup"] and not st["configured"])

    def test_define_pasta_cria_e_copia(self):
        self.add_data()
        backup.set_external_dir(self.db_path, str(self.ext))
        self.assertTrue(self.ext.is_dir())
        r = backup.copy_external(self.db_path, self.conn)
        self.assertTrue((self.ext / r["name"]).exists())
        backup.validate_backup(self.ext / r["name"])
        st = backup.external_status(self.db_path)
        self.assertTrue(st["configured"] and not st["stale"] and st["last_ok"])

    def test_caminho_relativo_e_pasta_do_proprio_app_sao_recusados(self):
        with self.assertRaises(ValueError):
            backup.set_external_dir(self.db_path, "relativa/pasta")
        with self.assertRaises(ValueError):
            backup.set_external_dir(self.db_path, str(backup.backup_dir(self.db_path)))
        with self.assertRaises(ValueError):
            backup.set_external_dir(self.db_path, "")

    def test_automatica_uma_vez_por_dia_e_nunca_levanta_erro(self):
        self.add_data()
        backup.set_external_dir(self.db_path, str(self.ext))
        today = datetime.now().strftime("%Y-%m-%d")
        self.assertTrue(backup.sync_external(self.db_path, today))    # primeira do dia
        self.assertFalse(backup.sync_external(self.db_path, today))   # no mesmo dia não repete
        # a pasta some (pen drive removido): a falha é registrada, sem exceção
        import shutil
        shutil.rmtree(self.ext.parent)
        cfg = backup._ext_load(self.db_path)
        cfg["last_ok"] = "2000-01-01 10:00"
        backup._ext_save(self.db_path, cfg)
        (self.ext.parent).write_text("arquivo no lugar da pasta")  # impede recriar
        self.assertFalse(backup.sync_external(self.db_path, today))
        st = backup.external_status(self.db_path)
        self.assertTrue(st["last_error"] and st["stale"])

    def test_mantem_apenas_as_ultimas_copias(self):
        self.add_data()
        backup.set_external_dir(self.db_path, str(self.ext))
        for i in range(backup.EXTERNAL_KEEP + 3):
            (self.ext / f"{backup.EXTERNAL_PREFIX}-2000010{i % 10}-{i:06d}.db").write_bytes(b"x")
        backup.copy_external(self.db_path, self.conn)
        self.assertEqual(len(list(self.ext.glob("*.db"))), backup.EXTERNAL_KEEP)

    def test_reset_esquece_a_pasta_mas_nao_apaga_o_que_esta_la(self):
        self.add_data()
        backup.set_external_dir(self.db_path, str(self.ext))
        name = backup.copy_external(self.db_path, self.conn)["name"]
        backup.reset(self.conn, self.db_path, {"confirm": "RESETAR", "wipe_backups": True})
        backup.external_wipe(self.db_path)
        self.assertFalse(backup.external_status(self.db_path)["configured"])
        self.assertTrue((self.ext / name).exists())
