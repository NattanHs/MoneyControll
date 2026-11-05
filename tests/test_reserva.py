"""Reserva de emergência sugerida: base de 6 meses, ajuste pelo perfil com precisão crescente."""
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site" / "py"))

from financas import accounts, db, finance, goals, perfil, reserva  # noqa: E402

TODAY = "2026-10-19"
DOZE_MESES = [f"2025-{m:02d}" for m in range(10, 13)] + [f"2026-{m:02d}" for m in range(1, 10)]


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

    def mes(self, ym, renda, gastos):
        """gastos: lista de (categoria, descrição, valor)."""
        finance.save_transaction(self.conn, {"date": f"{ym}-02", "kind": "entrada", "amount": renda, "description": "Salário"})
        for cat, desc, valor in gastos:
            finance.save_transaction(self.conn, {"date": f"{ym}-05", "kind": "saida", "amount": valor,
                                                 "description": desc, "category": cat})

    def meses(self, lista, renda, gasto_mes):
        for ym in lista:
            self.mes(ym, renda, gasto_mes)


class TestPerfilClassificacao(unittest.TestCase):
    def test_faixas(self):
        self.assertEqual(perfil.classificar("Alimentação", "iFood Lanche"), "alimentacao_fora")
        self.assertEqual(perfil.classificar("Alimentação", "Fast food McDonald's"), "alimentacao_fora")
        self.assertEqual(perfil.classificar("Alimentação", "Mercado Extra"), "essencial")
        self.assertEqual(perfil.classificar("Lazer", "Cinema"), "lazer")
        self.assertEqual(perfil.classificar("Casa", "Aluguel"), "essencial")
        self.assertEqual(perfil.classificar("Compras", "Shopee"), "superfluo")
        self.assertEqual(perfil.classificar("", "Juros do rotativo"), "divida")
        self.assertEqual(perfil.classificar("Outros", "Bicicleta"), "outros")

    def test_acentos_e_maiusculas_nao_importam(self):
        self.assertEqual(perfil.classificar("ALIMENTAÇÃO", "FAST FOOD"), "alimentacao_fora")
        self.assertEqual(perfil.classificar("Transporte", "UBER"), "essencial")


class TestCategoriasAmpliadas(unittest.TestCase):
    CASOS = {
        "essencial": ["Moradia", "Aluguel", "Condomínio", "Mercado", "Transporte", "Saúde", "Plano de saúde",
                      "Educação", "Escola", "Pets", "Ração", "Seguros", "Impostos", "IPVA", "Filhos", "Creche"],
        "lazer": ["Lazer", "Cinema", "Viagens", "Academia", "Assinaturas", "Streaming", "Bebidas", "Pet shop"],
        "superfluo": ["Compras", "Presentes", "Apostas", "Brinquedos"],
        "vestuario": ["Roupas", "Calçados", "Vestuário"],
        "eletronicos": ["Celular", "Eletrônicos", "Notebook"],
        "divida": ["Dívidas", "Juros", "Rotativo", "Cheque especial", "Empréstimo", "Multas"],
        "investimento": ["Investimentos", "Poupança", "Reserva"],
        "outros": ["Tarifas bancárias", "Cursos", "Móveis", "Doações", "Tecnologia", "Algo qualquer"],
    }

    def test_categorias_conhecidas(self):
        for faixa, nomes in self.CASOS.items():
            for nome in nomes:
                with self.subTest(categoria=nome):
                    self.assertEqual(perfil.classificar(nome), faixa)

    def test_alerta_na_descricao_vence_categoria_essencial(self):
        self.assertEqual(perfil.classificar("Alimentação", "iFood Lanche"), "alimentacao_fora")
        self.assertEqual(perfil.classificar("Mercado", "compra online Shopee"), "superfluo")
        self.assertEqual(perfil.classificar("Moradia", "juros do cheque especial"), "divida")

    def test_vestuario_so_e_supérfluo_no_excesso(self):
        meses = [{"ym": "2026-08", "renda": 3000, "faixas": {"vestuario": 400, "essencial": 0, "superfluo": 0},
                  "compras": []},
                 {"ym": "2026-09", "renda": 3000, "faixas": {"vestuario": 100, "essencial": 0, "superfluo": 0},
                  "compras": []}]
        perfil.resolver(meses)
        self.assertEqual(meses[0]["faixas"]["essencial"], 150)   # até 5% da renda (150) é essencial
        self.assertEqual(meses[0]["faixas"]["superfluo"], 250)   # o que passa do teto é supérfluo
        self.assertEqual(meses[1]["faixas"]["essencial"], 100)   # dentro do teto, nada é supérfluo
        self.assertEqual(meses[1]["faixas"]["superfluo"], 0)

    def test_celular_trocado_antes_de_3_anos_e_superfluo(self):
        meses = [{"ym": "2026-01", "renda": 5000, "faixas": {"eletronicos": 300, "essencial": 0, "superfluo": 0},
                  "compras": [("2026-01-10", 300)]},
                 {"ym": "2026-06", "renda": 5000, "faixas": {"eletronicos": 300, "essencial": 0, "superfluo": 0},
                  "compras": [("2026-06-10", 300)]}]
        perfil.resolver(meses)
        self.assertEqual(meses[0]["faixas"]["essencial"], 300)   # primeira compra é essencial
        self.assertEqual(meses[1]["faixas"]["superfluo"], 300)   # troca em 5 meses: supérfluo

    def test_celular_trocado_depois_de_3_anos_e_essencial(self):
        meses = [{"ym": "2023-01", "renda": 5000, "faixas": {"eletronicos": 300, "essencial": 0, "superfluo": 0},
                  "compras": [("2023-01-10", 300)]},
                 {"ym": "2026-03", "renda": 5000, "faixas": {"eletronicos": 300, "essencial": 0, "superfluo": 0},
                  "compras": [("2026-03-10", 300)]}]
        perfil.resolver(meses)
        self.assertEqual(meses[1]["faixas"]["essencial"], 300)   # 38 meses depois: troca normal
        self.assertEqual(meses[1]["faixas"]["superfluo"], 0)

    def test_eletronico_acima_do_teto_so_tem_o_excesso_como_superfluo(self):
        meses = [{"ym": "2026-02", "renda": 3000, "faixas": {"eletronicos": 900, "essencial": 0, "superfluo": 0},
                  "compras": [("2026-02-10", 900)]}]
        perfil.resolver(meses)
        self.assertEqual(meses[0]["faixas"]["essencial"], 450)   # teto de 15% da renda é essencial
        self.assertEqual(meses[0]["faixas"]["superfluo"], 450)   # o excesso, supérfluo

    def test_investimento_nao_pesa_na_qualidade(self):
        self.assertEqual(perfil.PESO["investimento"], 0)

    def test_saida_em_investimento_conta_como_poupanca_e_nao_gasto(self):
        base = Base(); base.setUp()
        try:
            finance.save_transaction(base.conn, {"date": "2026-09-05", "kind": "entrada", "amount": 3000, "description": "Salário"})
            finance.save_transaction(base.conn, {"date": "2026-09-06", "kind": "saida", "amount": 500,
                                                 "description": "Tesouro", "category": "Investimentos"})
            hist = reserva._historico(finance.load_all(base.conn), TODAY)
            self.assertEqual(hist[0]["gasto"], 0)
            self.assertEqual(hist[0]["aporte"], 500)
        finally:
            base.conn.close(); base.tmp.cleanup()


class TestSugestao(Base):
    def test_sem_historico_nao_sugere(self):
        self.assertFalse(reserva.suggest(self.conn, TODAY)["ok"])
        with self.assertRaises(ValueError):
            reserva.apply(self.conn, TODAY)

    def test_historico_curto_fica_na_base(self):
        self.mes("2026-09", 3000, [("Casa", "Aluguel", 2000)])
        s = reserva.suggest(self.conn, TODAY)
        self.assertEqual(s["meses_sugeridos"], 6)
        self.assertEqual(s["alvo_situacao"], 12000)

    def test_precisao_cresce_com_os_meses_lancados(self):
        precisoes = []
        for n in (1, 3, 6, 9, 12):
            self.conn.close()
            self.tmp.cleanup()
            self.setUp()
            self.meses(DOZE_MESES[:n], 3000, [("Casa", "Aluguel", 1500)])
            precisoes.append(reserva.suggest(self.conn, TODAY)["precisao"])
        self.assertEqual(precisoes, sorted(precisoes))
        self.assertEqual(precisoes[-1], 100)
        self.assertEqual(precisoes[0], 0)

    def test_organizado_com_12_meses_precisa_de_menos_folga(self):
        self.meses(DOZE_MESES, 3000, [("Casa", "Aluguel", 1200), ("Mercado", "Mercado Extra", 300)])
        s = reserva.suggest(self.conn, TODAY)
        self.assertEqual(s["meses_sugeridos"], 4)
        self.assertEqual(s["alvo_situacao"], 4 * 1500)
        self.assertEqual(s["alvo_renda"], 18000)

    def test_gasta_mais_que_ganha_com_12_meses_recebe_mais_folga(self):
        self.meses(DOZE_MESES, 3000, [("Casa", "Aluguel", 2000), ("Lazer", "Viagem", 1800)])
        self.assertEqual(reserva.suggest(self.conn, TODAY)["meses_sugeridos"], 9)

    def test_gastos_de_baixa_qualidade_pesam_no_perfil(self):
        self.meses(DOZE_MESES, 3000, [("Casa", "Aluguel", 1000), ("Mercado", "Mercado Extra", 1000)])
        bom = reserva._nota_perfil(reserva._historico(finance.load_all(self.conn), TODAY))
        self.conn.close(); self.tmp.cleanup(); self.setUp()
        self.meses(DOZE_MESES, 3000, [("Casa", "Aluguel", 1000), ("Alimentação", "iFood Lanche", 600),
                                      ("Compras", "Shopee", 400)])
        ruim = reserva._nota_perfil(reserva._historico(finance.load_all(self.conn), TODAY))
        self.assertLess(ruim, bom)

    def test_perfil_nao_aparece_na_resposta(self):
        self.meses(DOZE_MESES, 3000, [("Casa", "Aluguel", 1500)])
        s = reserva.suggest(self.conn, TODAY)
        for chave in s:
            for proibido in ("nota", "score", "perfil", "respons", "organiz", "faixa", "qualid"):
                self.assertNotIn(proibido, chave.lower())
        for texto in (s["message"],):
            for proibido in ("organiz", "respons", "perfil", "qualidade", "supérfl", "superfl"):
                self.assertNotIn(proibido, texto.lower())

    def test_mes_atual_nao_entra_no_historico(self):
        self.mes("2026-09", 3000, [("Casa", "Aluguel", 1500)])
        finance.save_transaction(self.conn, {"date": "2026-10-03", "kind": "saida", "amount": 99999, "description": "Enorme"})
        self.assertEqual(reserva.suggest(self.conn, TODAY)["custo_mensal"], 1500)


class TestAplicar(Base):
    def setUp(self):
        super().setUp()
        self.meses(DOZE_MESES, 3000, [("Casa", "Aluguel", 1500)])

    def test_cria_uma_reserva_com_o_alvo_da_sugestao(self):
        rid = reserva.apply(self.conn, TODAY)
        g = next(g for g in goals.list_goals(self.conn, TODAY) if g["id"] == rid)
        self.assertEqual(g["name"], reserva.RESERVA_NOME)
        self.assertEqual(g["target"], reserva.suggest(self.conn, TODAY)["alvo_situacao"])
        self.assertEqual(g["reserve"], 1)

    def test_aplicar_de_novo_atualiza_a_mesma_reserva(self):
        self.assertEqual(reserva.apply(self.conn, TODAY), reserva.apply(self.conn, TODAY))
        self.assertEqual(len([g for g in goals.list_goals(self.conn, TODAY) if g["reserve"]]), 1)

    def test_editar_pelo_formulario_nao_tira_a_marca_de_reserva(self):
        rid = reserva.apply(self.conn, TODAY)
        goals.save_goal(self.conn, {"name": "Reserva renomeada", "target": 7000, "saved": 0, "monthly": 300}, rid)
        g = next(g for g in goals.list_goals(self.conn, TODAY) if g["id"] == rid)
        self.assertEqual(g["reserve"], 1)
        self.assertEqual(g["target"], 7000)

    def test_lancamentos_para_a_reserva_contam_no_progresso(self):
        rid = reserva.apply(self.conn, TODAY)
        finance.save_transaction(self.conn, {"date": "2026-10-05", "kind": "saida", "amount": 500,
                                             "description": "Reserva", "goal_id": rid})
        g = next(g for g in goals.list_goals(self.conn, TODAY) if g["id"] == rid)
        self.assertEqual(g["saved"], 500)
        self.assertAlmostEqual(g["pct"], round(500 / g["target"] * 100, 1))


class TestEditarMovimentosDaReserva(Base):
    def setUp(self):
        super().setUp()
        self.meses(DOZE_MESES[-3:], 3000, [("Casa", "Aluguel", 1000)])
        self.rid = reserva.apply(self.conn, TODAY)
        self.tid = finance.save_transaction(self.conn, {"date": "2026-10-05", "kind": "saida", "amount": 500,
                                                        "description": "Guardar", "goal_id": self.rid})

    def test_editar_valor_do_movimento(self):
        finance.save_transaction(self.conn, {"date": "2026-10-05", "kind": "saida", "amount": 800,
                                             "description": "Guardar", "goal_id": self.rid}, self.tid)
        g = next(g for g in goals.list_goals(self.conn, TODAY) if g["id"] == self.rid)
        self.assertEqual(g["saved"], 800)

    def test_excluir_movimento(self):
        finance.delete_transaction(self.conn, self.tid)
        self.assertEqual(goals.movements(self.conn, self.rid), [])


if __name__ == "__main__":
    unittest.main()


class TestComposicaoEStatus(Base):
    def setUp(self):
        super().setUp()
        # renda 3000; essencial: aluguel 1200 + mercado 300; lazer 800 (não entra na base)
        self.meses(DOZE_MESES, 3000, [("Casa", "Aluguel", 1200), ("Mercado", "Mercado Extra", 300),
                                      ("Lazer", "Cinema", 800)])

    def test_base_e_o_custo_essencial_nao_o_total(self):
        s = reserva.suggest(self.conn, TODAY)
        self.assertEqual(s["custo_mensal"], 1500)           # aluguel + mercado, sem lazer
        self.assertEqual(s["alvo_situacao"], 1500 * s["meses_sugeridos"])

    def test_composicao_soma_o_custo_essencial(self):
        s = reserva.suggest(self.conn, TODAY)
        self.assertAlmostEqual(sum(c["mensal"] for c in s["composicao"]), s["custo_mensal"], places=0)
        self.assertEqual(s["composicao"][0]["categoria"], "Casa")
        self.assertEqual(sum(c["percentual"] for c in s["composicao"]), 100)

    def test_sem_reserva_nao_ha_status(self):
        self.assertIsNone(reserva.status(self.conn, TODAY))

    def test_cobertura_critica_e_aviso_de_importancia(self):
        reserva.apply(self.conn, TODAY)
        st = reserva.status(self.conn, TODAY)
        self.assertEqual(st["nivel"], "critico")           # 0 meses cobertos
        self.assertIn("tão importante", st["aviso"])
        self.assertIn("essenciais", st["aviso"])

    def test_aporte_nunca_passa_da_folga_real(self):
        rid = reserva.apply(self.conn, TODAY)
        finance.save_transaction(self.conn, {"date": "2026-10-05", "kind": "saida", "amount": 1000,
                                             "description": "Reserva", "goal_id": rid})
        st = reserva.status(self.conn, TODAY)
        self.assertLessEqual(st["aporte_sugerido"], st["folga_mensal"] + 0.01)
        self.assertEqual(st["aporte_sugerido"], min(round(st["falta"] / 12, 2), st["folga_mensal"]))

    def test_prazo_e_honesto_quando_a_folga_nao_cobre_12_meses(self):
        for ym in DOZE_MESES:  # mais lazer: a sobra média cai para R$ 100 (folga de R$ 50)
            finance.save_transaction(self.conn, {"date": f"{ym}-20", "kind": "saida", "amount": 600,
                                                 "description": "Viagem", "category": "Lazer"})
        self.conn.execute("UPDATE accounts SET opening_balance = 0")
        reserva.apply(self.conn, TODAY)
        st = reserva.status(self.conn, TODAY)
        self.assertFalse(st["aporte_cabe"])
        self.assertEqual(st["aporte_sugerido"], st["folga_mensal"])
        self.assertEqual(st["prazo_meses"], math.ceil(st["falta"] / st["folga_mensal"]))

    def test_sem_folga_nao_sugere_aporte(self):
        for ym in DOZE_MESES:  # saídas acima da renda
            finance.save_transaction(self.conn, {"date": f"{ym}-20", "kind": "saida", "amount": 2000,
                                                 "description": "Extra", "category": "Lazer"})
        reserva.apply(self.conn, TODAY)
        st = reserva.status(self.conn, TODAY)
        self.assertEqual(st["aporte_sugerido"], 0)
        self.assertIsNone(st["prazo_meses"])
        self.assertIn("Reduza", st["texto_aporte"])

    def test_reserva_na_meta_fica_ok(self):
        rid = reserva.apply(self.conn, TODAY)
        alvo = reserva.suggest(self.conn, TODAY)["alvo_situacao"]
        finance.save_transaction(self.conn, {"date": "2026-10-05", "kind": "saida", "amount": alvo,
                                             "description": "Reserva", "goal_id": rid})
        st = reserva.status(self.conn, TODAY)
        self.assertEqual(st["nivel"], "ok")
        self.assertEqual(st["falta"], 0)
