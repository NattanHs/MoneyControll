"""Retrato financeiro: renda, folga e dívida lidos do histórico."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site" / "py"))

from financas import accounts, cards, db, finance, limites, retrato  # noqa: E402

TODAY = "2026-10-19"
DOZE = [f"2025-{m:02d}" for m in range(10, 13)] + [f"2026-{m:02d}" for m in range(1, 10)]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "f.db")
        db.init(self.conn)
        self.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('opening_month', '2025-01')")
        accounts.default_id(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def mes(self, ym, renda, gastos=(), card_id=None):
        if renda:
            finance.save_transaction(self.conn, {"date": f"{ym}-02", "kind": "entrada", "amount": renda, "description": "Renda"})
        for cat, desc, valor in gastos:
            finance.save_transaction(self.conn, {"date": f"{ym}-05", "kind": "saida", "amount": valor,
                                                 "description": desc, "category": cat})

    def retrato(self):
        return retrato.construir(finance.load_all(self.conn), TODAY)


class TestPoucosDados(Base):
    def test_sem_historico(self):
        r = self.retrato()
        self.assertFalse(r["ok"])
        self.assertEqual(r["meses"], 0)

    def test_dois_meses_nao_bastam(self):
        for ym in DOZE[-2:]:
            self.mes(ym, 5000, [("Moradia", "Aluguel", 2000)])
        r = self.retrato()
        self.assertFalse(r["ok"])
        self.assertEqual(r["meses"], 2)
        self.assertEqual(r["confianca"], 0.0)

    def test_tres_meses_ja_formam_o_retrato_com_pouca_confianca(self):
        for ym in DOZE[-3:]:
            self.mes(ym, 5000, [("Moradia", "Aluguel", 2000)])
        r = self.retrato()
        self.assertTrue(r["ok"])
        self.assertEqual(r["meses"], 3)
        self.assertAlmostEqual(r["confianca"], 0.1)

    def test_doze_meses_confianca_total(self):
        for ym in DOZE:
            self.mes(ym, 5000, [("Moradia", "Aluguel", 2000)])
        self.assertEqual(self.retrato()["confianca"], 1.0)

    def test_mes_atual_nao_entra(self):
        for ym in DOZE[-3:]:
            self.mes(ym, 5000, [("Moradia", "Aluguel", 2000)])
        self.mes("2026-10", 100, [("Lazer", "Cinema", 900)])
        self.assertEqual(self.retrato()["meses"], 3)


class TestRenda(Base):
    def test_clt_estavel_e_folgado(self):
        for ym in DOZE:
            self.mes(ym, 5000, [("Moradia", "Aluguel", 1800), ("Mercado", "Mercado", 1000)])
        r = self.retrato()
        self.assertEqual(r["renda"]["estado"], "estavel")
        self.assertEqual(r["renda"]["base"], 5000)
        self.assertEqual(r["folga"]["estado"], "folgado")
        self.assertEqual(r["divida"]["estado"], "sem_divida_cara")

    def test_mei_irregular_usa_o_pior_mes_como_base(self):
        rendas = [2000, 6500, 3000, 9000, 1500, 7000, 2500, 8000, 3500, 6000, 2200, 7500]
        for ym, renda in zip(DOZE, rendas):
            self.mes(ym, renda, [("Moradia", "Aluguel", 1200)])
        r = self.retrato()
        self.assertEqual(r["renda"]["estado"], "irregular")
        self.assertEqual(r["renda"]["base"], 1500)
        self.assertGreater(r["renda"]["cv"], limites.RENDA_CV_IRREGULAR)

    def test_renda_variavel_entre_os_limiares(self):
        rendas = [4500, 5500] * 6  # variação de 10% exatos fica em "variavel"
        for ym, renda in zip(DOZE, rendas):
            self.mes(ym, renda, [("Moradia", "Aluguel", 1500)])
        self.assertEqual(self.retrato()["renda"]["estado"], "variavel")

    def test_renda_quase_igual_e_estavel(self):
        for ym, renda in zip(DOZE, [5000, 5050] * 6):
            self.mes(ym, renda, [("Moradia", "Aluguel", 1500)])
        self.assertEqual(self.retrato()["renda"]["estado"], "estavel")

    def test_mes_sem_renda_pesa_como_irregular(self):
        for ym in DOZE[:-1]:
            self.mes(ym, 5000, [("Moradia", "Aluguel", 1500)])
        self.mes(DOZE[-1], 0, [("Moradia", "Aluguel", 1500)])
        r = self.retrato()
        self.assertEqual(r["renda"]["estado"], "irregular")
        self.assertEqual(r["renda"]["base"], 0)


class TestFolga(Base):
    def _meses(self, renda, gasto):
        for ym in DOZE:
            self.mes(ym, renda, [("Moradia", "Aluguel", gasto)])
        return self.retrato()["folga"]

    def test_vermelho_quando_gasta_mais_que_ganha(self):
        f = self._meses(4000, 4300)
        self.assertEqual(f["estado"], "vermelho")
        self.assertLess(f["taxa"], 0)

    def test_apertado_abaixo_de_5_por_cento(self):
        self.assertEqual(self._meses(4000, 3900)["estado"], "apertado")  # sobra 2,5%

    def test_equilibrado_entre_5_e_15(self):
        self.assertEqual(self._meses(4000, 3600)["estado"], "equilibrado")  # sobra 10%

    def test_folgado_a_partir_de_15(self):
        self.assertEqual(self._meses(4000, 3400)["estado"], "folgado")  # sobra exatamente 15%

    def test_limite_de_5_por_cento_ja_e_equilibrado(self):
        self.assertEqual(self._meses(4000, 3800)["estado"], "equilibrado")  # sobra exatamente 5%

    def test_tres_meses_negativos_em_doze_marca_vermelho_mesmo_com_media_boa(self):
        for i, ym in enumerate(DOZE):
            self.mes(ym, 6000, [("Moradia", "Aluguel", 6600 if i in (2, 5, 8) else 3000)])
        f = self.retrato()["folga"]
        self.assertEqual(f["meses_negativos"], 3)
        self.assertGreater(f["taxa"], 0.15)
        self.assertEqual(f["estado"], "vermelho")

    def test_dois_meses_negativos_em_doze_nao_marca_vermelho(self):
        for i, ym in enumerate(DOZE):
            self.mes(ym, 6000, [("Moradia", "Aluguel", 6600 if i in (2, 5) else 3000)])
        self.assertEqual(self.retrato()["folga"]["estado"], "folgado")

    def test_conta_no_vermelho_no_ultimo_mes_marca_vermelho(self):
        for ym in DOZE[:-1]:
            self.mes(ym, 6000, [("Moradia", "Aluguel", 3000)])
        self.mes(DOZE[-1], 6000, [("Moradia", "Aluguel", 90000)])
        f = self.retrato()["folga"]
        self.assertTrue(f["conta_negativa"])
        self.assertEqual(f["estado"], "vermelho")

    def test_investir_nao_conta_como_gasto(self):
        for ym in DOZE:
            self.mes(ym, 5000, [("Moradia", "Aluguel", 3000), ("Investimentos", "Tesouro Selic", 1500)])
        f = self.retrato()["folga"]
        self.assertEqual(f["estado"], "folgado")
        self.assertAlmostEqual(f["taxa"], 0.4)


class TestDivida(Base):
    def test_juros_recorrentes_marcam_divida_cara(self):
        for i, ym in enumerate(DOZE):
            gastos = [("Moradia", "Aluguel", 1500)]
            if i >= 9:
                gastos.append(("Dívidas", "Juros do rotativo", 300))
            self.mes(ym, 5000, gastos)
        d = self.retrato()["divida"]
        self.assertEqual(d["estado"], "divida_cara")
        self.assertEqual(d["custo_mensal"], 300)
        self.assertEqual(d["nivel"], "normal")

    def test_juros_em_um_so_mes_dos_ultimos_tres_nao_e_recorrente(self):
        for i, ym in enumerate(DOZE):
            gastos = [("Moradia", "Aluguel", 1500)]
            if i == 11:
                gastos.append(("Dívidas", "Juros do rotativo", 300))
            self.mes(ym, 5000, gastos)
        self.assertEqual(self.retrato()["divida"]["estado"], "sem_divida_cara")

    def test_divida_antiga_fora_da_janela_nao_conta(self):
        for i, ym in enumerate(DOZE):
            gastos = [("Moradia", "Aluguel", 1500)]
            if i < 6:
                gastos.append(("Dívidas", "Juros do rotativo", 400))
            self.mes(ym, 5000, gastos)
        self.assertEqual(self.retrato()["divida"]["estado"], "sem_divida_cara")

    def test_divida_cara_com_30_por_cento_da_renda_e_comprometida(self):
        for ym in DOZE:
            self.mes(ym, 5000, [("Moradia", "Aluguel", 1500), ("Dívidas", "Empréstimo", 1500)])
        d = self.retrato()["divida"]
        self.assertEqual(d["estado"], "comprometida")
        self.assertEqual(d["nivel"], "alto")

    def test_metade_da_renda_em_divida_e_critico(self):
        for ym in DOZE:
            self.mes(ym, 4000, [("Moradia", "Aluguel", 1000), ("Dívidas", "Empréstimo", 2000)])
        d = self.retrato()["divida"]
        self.assertEqual(d["estado"], "comprometida")
        self.assertEqual(d["nivel"], "critico")

    def test_parcelas_pesam_no_comprometimento_sem_virar_divida_cara(self):
        for ym in DOZE:
            self.mes(ym, 5000, [("Moradia", "Aluguel", 1500)])
        finance.save_installment(self.conn, {"description": "Geladeira", "installment_amount": 1800, "category": "Casa",
                                             "first_date": "2025-09-10", "n_installments": 24})
        d = self.retrato()["divida"]
        self.assertEqual(d["estado"], "sem_divida_cara")
        self.assertEqual(d["nivel"], "alto")  # 36% da renda
        self.assertAlmostEqual(d["comprometimento"], 0.36)

    def test_parcela_no_cartao_tambem_conta(self):
        cid = cards.save_card(self.conn, {"name": "Cartão", "closing_day": 10, "due_day": 17})
        for ym in DOZE:
            self.mes(ym, 5000, [("Moradia", "Aluguel", 1500)])
        finance.save_installment(self.conn, {"description": "TV", "installment_amount": 500, "category": "Casa",
                                             "first_date": "2025-09-05", "n_installments": 24, "card_id": cid})
        self.assertGreater(self.retrato()["divida"]["comprometimento"], 0.05)

    def test_parcela_de_emprestimo_nao_conta_duas_vezes(self):
        for ym in DOZE:
            self.mes(ym, 5000, [("Moradia", "Aluguel", 1500)])
        finance.save_installment(self.conn, {"description": "Empréstimo pessoal", "installment_amount": 500, "category": "Empréstimo",
                                             "first_date": "2025-09-10", "n_installments": 24})
        d = self.retrato()["divida"]
        self.assertAlmostEqual(d["comprometimento"], 0.10)  # 500 / 5000, contado uma vez
        self.assertEqual(d["estado"], "divida_cara")


class TestSeguranca(Base):
    def test_construir_nao_grava_nada(self):
        for ym in DOZE:
            self.mes(ym, 5000, [("Moradia", "Aluguel", 1500)])
        antes = self.conn.total_changes
        self.retrato()
        self.assertEqual(self.conn.total_changes, antes)

    def test_reserva_continua_usando_o_mesmo_historico(self):
        from financas import reserva
        for ym in DOZE:
            self.mes(ym, 5000, [("Moradia", "Aluguel", 1500)])
        data = finance.load_all(self.conn)
        self.assertEqual(reserva._historico(data, TODAY), retrato.historico(data, TODAY))


class TestIntegracao(Base):
    def _rotativo(self):
        for i, ym in enumerate(DOZE):
            gastos = [("Moradia", "Aluguel", 1500)]
            if i >= 8:
                gastos.append(("Dívidas", "Juros do rotativo", 500))
            self.mes(ym, 5000, gastos)

    def test_visao_geral_usa_o_retrato(self):
        from financas import reports
        self._rotativo()
        self.mes("2026-10", 5000, [("Moradia", "Aluguel", 1500)])
        h = reports.overview(self.conn, TODAY)["health"]
        self.assertEqual(h["base_meses"], 12)
        self.assertEqual(h["level"], "ok")  # folga alta, mas dívida cara impede "saudável"
        self.assertIn("quit", h["tip"].lower())

    def test_mes_a_mes_usa_o_retrato(self):
        self._rotativo()
        self.mes("2026-10", 5000, [("Moradia", "Aluguel", 1500)])
        h = finance.month_view(self.conn, "2026-10", TODAY)["health"]
        self.assertEqual(h["base_meses"], 12)

    def test_sem_historico_a_saude_continua_como_antes(self):
        from financas import reports
        self.mes("2026-10", 5000, [("Moradia", "Aluguel", 1500)])
        h = reports.overview(self.conn, TODAY)["health"]
        self.assertNotIn("base_meses", h)
        self.assertEqual(h["level"], "saudavel")

    def test_falha_no_retrato_nao_derruba_a_saude(self):
        from financas import reports
        self._rotativo()
        self.mes("2026-10", 5000, [("Moradia", "Aluguel", 1500)])
        original = retrato.construir
        retrato.construir = lambda *a, **k: 1 / 0
        try:
            self.assertIsNone(retrato.seguro(finance.load_all(self.conn), TODAY))
            h = reports.overview(self.conn, TODAY)["health"]
            self.assertNotIn("base_meses", h)
            self.assertEqual(h["level"], "saudavel")
        finally:
            retrato.construir = original


class TestDesempenho(Base):
    def test_cinco_anos_de_historico_respondem_rapido(self):
        import time
        meses = [f"{y}-{m:02d}" for y in range(2021, 2026) for m in range(1, 13)]
        self.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('opening_month', '2021-01')")
        for ym in meses:
            self.mes(ym, 6000, [("Moradia", "Aluguel", 1800), ("Mercado", "Mercado", 1200), ("Lazer", "Cinema", 300)])
        for ym in [f"2026-{m:02d}" for m in range(1, 10)]:
            self.mes(ym, 6000, [("Moradia", "Aluguel", 1800), ("Mercado", "Mercado", 1200)])
        data = finance.load_all(self.conn)
        t = time.perf_counter()
        r = retrato.construir(data, TODAY)
        dt = time.perf_counter() - t
        self.assertTrue(r["ok"])
        print(f"\n  retrato com 69 meses de histórico: {dt * 1000:.0f} ms")
        self.assertLess(dt, 3.0)


if __name__ == "__main__":
    unittest.main()
