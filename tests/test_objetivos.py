"""Objetivos de poupança: progresso, quanto falta e data estimada."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site" / "py"))

from financas import db, finance, goals, reports  # noqa: E402

TODAY = "2026-10-19"


class TestObjetivos(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "f.db")
        db.init(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def novo(self, target, saved=0, monthly=0, name="Viagem"):
        gid = goals.save_goal(self.conn, {"name": name, "target": target, "saved": saved, "monthly": monthly})
        return next(g for g in goals.list_goals(self.conn, TODAY) if g["id"] == gid)

    def test_previsao_arredonda_meses_para_cima(self):
        g = self.novo(10000, saved=1000, monthly=800)  # falta 9000 / 800 = 11,25 -> 12 meses
        self.assertEqual(g["remaining"], 9000)
        self.assertEqual(g["months_left"], 12)
        self.assertEqual(g["eta"], "2027-10")  # 2026-10 + 12 meses
        self.assertEqual(g["status"], "em_andamento")

    def test_progresso_percentual(self):
        self.assertEqual(self.novo(4000, saved=1000, monthly=100)["pct"], 25.0)

    def test_concluido_quando_ja_atingiu(self):
        g = self.novo(500, saved=600, monthly=100)
        self.assertEqual(g["status"], "concluido")
        self.assertEqual(g["pct"], 100.0)
        self.assertIsNone(g["eta"])

    def test_sem_aporte_nao_inventa_data(self):
        g = self.novo(3000, saved=500, monthly=0)
        self.assertEqual(g["status"], "sem_aporte")
        self.assertIsNone(g["eta"])
        self.assertIsNone(g["months_left"])

    def test_edicao_atualiza_o_progresso(self):
        gid = goals.save_goal(self.conn, {"name": "Carro", "target": 2000, "saved": 0, "monthly": 500})
        goals.save_goal(self.conn, {"name": "Carro", "target": 2000, "saved": 1500, "monthly": 500}, gid)
        g = goals.list_goals(self.conn, TODAY)[0]
        self.assertEqual(g["pct"], 75.0)
        self.assertEqual(g["eta"], "2026-11")

    def test_valida_campos(self):
        with self.assertRaises(ValueError):
            goals.save_goal(self.conn, {"name": "", "target": 100})
        with self.assertRaises(ValueError):
            goals.save_goal(self.conn, {"name": "X", "target": 0})
        with self.assertRaises(ValueError):
            goals.save_goal(self.conn, {"name": "X", "target": 100, "monthly": -5})

    def test_excluir(self):
        gid = goals.save_goal(self.conn, {"name": "X", "target": 100})
        goals.delete_goal(self.conn, gid)
        self.assertEqual(goals.list_goals(self.conn, TODAY), [])



class TestObjetivoComRendimento(unittest.TestCase):
    """Objetivo que rende em % do CDI: a previsão considera os juros (líquidos do IR estimado)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "f.db")
        db.init(self.conn)
        db.set_setting(self.conn, "cdi_annual", 14.5)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def novo(self, target, saved=0, monthly=0, **extra):
        gid = goals.save_goal(self.conn, {"name": "Viagem", "target": target, "saved": saved, "monthly": monthly, **extra})
        return next(g for g in goals.list_goals(self.conn, TODAY) if g["id"] == gid)

    def test_sem_a_caixa_marcada_nada_muda(self):
        g = self.novo(10000, saved=1000, monthly=800)
        self.assertEqual(g["earns"], 0)
        self.assertEqual(g["months_left"], 12)
        self.assertIsNone(g["rate_annual"])
        self.assertIsNone(g["months_no_yield"])

    def test_rendimento_encurta_o_prazo(self):
        sem = self.novo(60000, saved=5000, monthly=1500)
        com = self.novo(60000, saved=5000, monthly=1500, earns=True, pct_cdi=100)
        self.assertLess(com["months_left"], sem["months_left"])
        self.assertEqual(com["months_no_yield"], sem["months_left"])
        self.assertEqual(com["status"], "em_andamento")
        self.assertEqual(com["rate_annual"], 14.5)

    def test_zero_por_cento_do_cdi_equivale_a_nao_render(self):
        sem = self.novo(10000, saved=1000, monthly=800)
        zero = self.novo(10000, saved=1000, monthly=800, earns=True, pct_cdi=0.01)
        self.assertEqual(zero["months_left"], sem["months_left"])

    def test_percentual_maior_rende_mais(self):
        a = self.novo(60000, saved=5000, monthly=1500, earns=True, pct_cdi=80)
        b = self.novo(60000, saved=5000, monthly=1500, earns=True, pct_cdi=120)
        self.assertLessEqual(b["months_left"], a["months_left"])
        self.assertEqual(b["rate_annual"], 17.4)

    def test_previsao_e_liquida_do_ir_estimado(self):
        # 10.000 parados a 100% do CDI: bruto chega à meta antes do que o líquido
        g = self.novo(10600, saved=10000, monthly=0, earns=True, pct_cdi=100)
        # bruto em 4,5% a.a. ~ 6 meses para 4,3%: o líquido (IR 22,5%) precisa de mais tempo que o bruto
        bruto_meses = None
        saldo = 10000.0
        taxa = (1 + 0.145) ** (1 / 12) - 1
        for t in range(1, 200):
            saldo *= 1 + taxa
            if saldo >= 10600:
                bruto_meses = t
                break
        self.assertGreater(g["months_left"], bruto_meses)

    def test_sem_aporte_mas_rendendo_ainda_tem_data(self):
        g = self.novo(11000, saved=10000, monthly=0, earns=True, pct_cdi=100)
        self.assertEqual(g["status"], "em_andamento")
        self.assertIsNotNone(g["eta"])

    def test_sem_aporte_e_sem_saldo_continua_sem_previsao(self):
        g = self.novo(5000, saved=0, monthly=0, earns=True, pct_cdi=100)
        self.assertEqual(g["status"], "sem_aporte")
        self.assertIsNone(g["eta"])

    def test_meta_ja_atingida_continua_concluida(self):
        g = self.novo(1000, saved=1500, monthly=100, earns=True, pct_cdi=100)
        self.assertEqual(g["status"], "concluido")

    def test_meta_enorme_nao_trava_e_cai_na_conta_sem_rendimento(self):
        g = self.novo(1_000_000_000_000, saved=0, monthly=1, earns=True, pct_cdi=100)
        self.assertEqual(g["status"], "em_andamento")
        self.assertGreater(g["months_left"], 1200)

    def test_cdi_zero_nas_configuracoes_equivale_a_nao_render(self):
        db.set_setting(self.conn, "cdi_annual", 0)
        g = self.novo(10000, saved=1000, monthly=800, earns=True, pct_cdi=100)
        self.assertEqual(g["months_left"], 12)

    def test_validacao_do_percentual(self):
        for ruim in (0, -5, 1001, "abc", "", 0.001):
            with self.subTest(pct=ruim):
                with self.assertRaises(ValueError):
                    goals.save_goal(self.conn, {"name": "X", "target": 100, "earns": True, "pct_cdi": ruim})

    def test_marcar_sem_informar_o_percentual_usa_100(self):
        g = self.novo(10000, saved=1000, monthly=800, earns=True)
        self.assertEqual(g["pct_cdi"], 100)

    def test_editar_sem_enviar_a_caixa_preserva_o_rendimento(self):
        g = self.novo(10000, saved=1000, monthly=800, earns=True, pct_cdi=90)
        goals.save_goal(self.conn, {"name": "Viagem", "target": 12000, "saved": 1000, "monthly": 800}, g["id"])
        g2 = next(x for x in goals.list_goals(self.conn, TODAY) if x["id"] == g["id"])
        self.assertEqual((g2["earns"], g2["pct_cdi"]), (1, 90))

    def test_desmarcar_a_caixa_desliga_o_rendimento(self):
        g = self.novo(10000, saved=1000, monthly=800, earns=True, pct_cdi=90)
        goals.save_goal(self.conn, {"name": "Viagem", "target": 10000, "saved": 1000, "monthly": 800, "earns": False}, g["id"])
        g2 = next(x for x in goals.list_goals(self.conn, TODAY) if x["id"] == g["id"])
        self.assertEqual(g2["earns"], 0)
        self.assertEqual(g2["months_left"], 12)

    def test_banco_antigo_sem_as_colunas_e_migrado(self):
        self.conn.close()
        antigo = Path(self.tmp.name) / "antigo.db"
        c = db.connect(antigo)
        db.init(c)
        c.execute("ALTER TABLE goals DROP COLUMN earns")
        c.execute("ALTER TABLE goals DROP COLUMN pct_cdi")
        c.execute("INSERT INTO goals(name, target, saved, monthly) VALUES ('Velho', 1000, 100, 100)")
        c.commit()
        c.close()
        c = db.connect(antigo)
        db.init(c)
        g = goals.list_goals(c, TODAY)[0]
        self.assertEqual((g["earns"], g["pct_cdi"]), (0, 100))
        c.close()
        self.conn = db.connect(Path(self.tmp.name) / "f.db")


if __name__ == "__main__":
    unittest.main()


class TestLancamentoParaObjetivo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "f.db")
        db.init(self.conn)
        self.gid = goals.save_goal(self.conn, {"name": "Viagem", "target": 1000, "saved": 100, "monthly": 200})

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def guardado(self):
        return next(g for g in goals.list_goals(self.conn, TODAY) if g["id"] == self.gid)["saved"]

    def lanca(self, amount, kind="saida", goal=None, date="2026-10-05", **extra):
        return finance.save_transaction(self.conn, {"date": date, "kind": kind, "amount": amount,
                                                    "description": "Poupança", "goal_id": goal, **extra})

    def test_saida_vinculada_soma_no_progresso(self):
        self.lanca(300, goal=self.gid)
        self.assertEqual(self.guardado(), 400)  # 100 já guardado + 300 lançado
        g = goals.list_goals(self.conn, TODAY)[0]
        self.assertEqual(g["saved_base"], 100)
        self.assertEqual(g["saved_linked"], 300)
        self.assertEqual(g["pct"], 40.0)

    def test_nao_conta_como_gasto_e_aparece_como_aporte(self):
        self.lanca(300, goal=self.gid)
        s = finance.month_view(self.conn, "2026-10", TODAY)["summary"]
        self.assertEqual(s["saidas"], 0)
        self.assertEqual(s["aportes"], 300)

    def test_item_leva_o_nome_do_objetivo(self):
        self.lanca(300, goal=self.gid)
        itens = finance.month_view(self.conn, "2026-10", TODAY)["items"]
        vinc = [i for i in itens if i.get("goal_name")]
        self.assertEqual(len(vinc), 1)
        self.assertEqual(vinc[0]["goal_name"], "Viagem")

    def test_editar_valor_atualiza_o_progresso(self):
        tid = self.lanca(300, goal=self.gid)
        finance.save_transaction(self.conn, {"date": "2026-10-05", "kind": "saida", "amount": 150,
                                             "description": "Poupança", "goal_id": self.gid}, tid)
        self.assertEqual(self.guardado(), 250)

    def test_excluir_lancamento_desfaz_o_progresso(self):
        tid = self.lanca(300, goal=self.gid)
        finance.delete_transaction(self.conn, tid)
        self.assertEqual(self.guardado(), 100)

    def test_desvincular_remove_do_objetivo_e_vira_gasto_normal(self):
        tid = self.lanca(300, goal=self.gid)
        finance.save_transaction(self.conn, {"date": "2026-10-05", "kind": "saida", "amount": 300,
                                             "description": "Poupança", "goal_id": None}, tid)
        self.assertEqual(self.guardado(), 100)
        s = finance.month_view(self.conn, "2026-10", TODAY)["summary"]
        self.assertEqual(s["saidas"], 300)

    def test_retirada_do_objetivo_reduz_o_progresso(self):
        self.lanca(40, kind="entrada", goal=self.gid)  # saved base 100 -> retirar 40
        self.assertEqual(self.guardado(), 60)

    def test_retirada_acima_do_disponivel_e_rejeitada(self):
        with self.assertRaises(ValueError):
            self.lanca(500, kind="entrada", goal=self.gid)  # só há 100 disponíveis

    def test_retirada_volta_ao_caixa_quando_sai_da_conta(self):
        self.lanca(40, kind="entrada", goal=self.gid)
        itens = [i for i in finance.month_view(self.conn, "2026-10", TODAY)["items"] if i.get("goal_name")]
        self.assertEqual(itens[0]["amount"], -40)  # sinal negativo: dinheiro voltou para a conta

    def test_objetivo_inexistente_e_rejeitado(self):
        with self.assertRaises(ValueError):
            self.lanca(10, goal=9999)

    def test_nao_exclui_objetivo_com_lancamentos(self):
        self.lanca(300, goal=self.gid)
        with self.assertRaises(ValueError):
            goals.delete_goal(self.conn, self.gid)


class TestPatrimonioObjetivos(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "f.db")
        db.init(self.conn)
        self.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('opening_month', '2026-10')")
        from financas import accounts
        accounts.default_id(self.conn)
        self.conn.execute("UPDATE accounts SET opening_balance = 1000")
        self.gid = goals.save_goal(self.conn, {"name": "Viagem", "target": 2000, "saved": 0, "monthly": 0})

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_aporte_sai_do_saldo_e_entra_no_patrimonio_como_objetivo(self):
        antes = reports.overview(self.conn, TODAY)
        finance.save_transaction(self.conn, {"date": "2026-10-05", "kind": "saida", "amount": 300,
                                             "description": "Poupança", "goal_id": self.gid})
        depois = reports.overview(self.conn, TODAY)
        self.assertEqual(depois["conta"], antes["conta"] - 300)      # disponível em conta diminui
        self.assertEqual(depois["objetivos"], 300)                   # aparece como objetivo
        self.assertAlmostEqual(depois["patrimonio_total"], antes["patrimonio_total"], places=2)
        self.assertAlmostEqual(depois["investido"], antes["investido"] + 300, places=2)

    def test_saldo_inicial_de_objetivo_nao_duplica_patrimonio(self):
        goals.save_goal(self.conn, {"name": "Carro", "target": 5000, "saved": 800, "monthly": 0})
        o = reports.overview(self.conn, TODAY)
        self.assertEqual(o["objetivos"], 0)  # "já guardado antes" não foi retirado da conta: não soma de novo

    def test_patrimonio_e_soma_das_partes(self):
        finance.save_transaction(self.conn, {"date": "2026-10-05", "kind": "saida", "amount": 150,
                                             "description": "Poupança", "goal_id": self.gid})
        o = reports.overview(self.conn, TODAY)
        self.assertAlmostEqual(o["patrimonio_total"], o["conta"] + o["renda_variavel"] + o["renda_fixa"] + o["objetivos"], places=2)


class TestDepositoForaDaConta(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "f.db")
        db.init(self.conn)
        self.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('opening_month', '2026-10')")
        from financas import accounts
        accounts.default_id(self.conn)
        self.conn.execute("UPDATE accounts SET opening_balance = 1000")
        self.gid = goals.save_goal(self.conn, {"name": "Viagem", "target": 2000, "saved": 0, "monthly": 0})

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def lanca(self, cash):
        return finance.save_transaction(self.conn, {"date": "2026-10-05", "kind": "saida", "amount": 300,
                                                    "description": "Depósito", "goal_id": self.gid, "goal_cash": cash})

    def test_padrao_sai_da_conta(self):
        antes = reports.overview(self.conn, TODAY)
        self.lanca(True)
        self.assertEqual(reports.overview(self.conn, TODAY)["conta"], antes["conta"] - 300)

    def test_deposito_especifico_nao_reduz_o_saldo_principal(self):
        antes = reports.overview(self.conn, TODAY)
        self.lanca(False)
        depois = reports.overview(self.conn, TODAY)
        self.assertEqual(depois["conta"], antes["conta"])          # saldo principal intacto
        self.assertEqual(depois["objetivos"], 300)                  # objetivo recebe o valor
        self.assertAlmostEqual(depois["patrimonio_total"], antes["patrimonio_total"] + 300, places=2)

    def test_deposito_especifico_nao_conta_como_gasto_nem_aporte_de_caixa(self):
        self.lanca(False)
        s = finance.month_view(self.conn, "2026-10", TODAY)["summary"]
        self.assertEqual(s["saidas"], 0)
        self.assertEqual(s["aportes"], 0)
        self.assertEqual(s["saldo_final"], 1000)

    def test_item_marcado_como_fora_da_conta(self):
        self.lanca(False)
        itens = [i for i in finance.month_view(self.conn, "2026-10", TODAY)["items"] if i.get("goal_name")]
        self.assertTrue(itens[0]["no_cash"])

    def test_editar_para_sair_da_conta_volta_a_reduzir_o_saldo(self):
        tid = self.lanca(False)
        finance.save_transaction(self.conn, {"date": "2026-10-05", "kind": "saida", "amount": 300,
                                             "description": "Depósito", "goal_id": self.gid, "goal_cash": True}, tid)
        self.assertEqual(finance.month_view(self.conn, "2026-10", TODAY)["summary"]["saldo_final"], 700)


class TestMovimentosEReserva(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "f.db")
        db.init(self.conn)
        self.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('opening_month', '2026-10')")
        from financas import accounts
        accounts.default_id(self.conn)
        self.conn.execute("UPDATE accounts SET opening_balance = 1000")
        self.rid = goals.save_goal(self.conn, {"name": "Reserva de emergência", "target": 6000, "saved": 0,
                                               "monthly": 0, "reserve": 1})

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_movimentos_listam_guardar_e_retirar(self):
        finance.save_transaction(self.conn, {"date": "2026-10-05", "kind": "saida", "amount": 500, "description": "Guardar", "goal_id": self.rid})
        finance.save_transaction(self.conn, {"date": "2026-10-07", "kind": "entrada", "amount": 200, "description": "Retirar", "goal_id": self.rid})
        mov = goals.movements(self.conn, self.rid)
        self.assertEqual(len(mov), 2)
        self.assertEqual(goals.list_goals(self.conn, TODAY)[0]["saved"], 300)

    def test_tile_da_reserva_no_overview(self):
        finance.save_transaction(self.conn, {"date": "2026-10-05", "kind": "saida", "amount": 500, "description": "Guardar", "goal_id": self.rid})
        o = reports.overview(self.conn, TODAY)
        self.assertEqual(o["reserva"]["saved"], 500)
        self.assertEqual(o["reserva"]["target"], 6000)

    def test_retirada_nao_conta_como_receita(self):
        finance.save_transaction(self.conn, {"date": "2026-10-05", "kind": "saida", "amount": 500, "description": "Guardar", "goal_id": self.rid})
        finance.save_transaction(self.conn, {"date": "2026-10-07", "kind": "entrada", "amount": 200, "description": "Retirar", "goal_id": self.rid})
        self.assertEqual(finance.month_view(self.conn, "2026-10", TODAY)["summary"]["entradas"], 0)
