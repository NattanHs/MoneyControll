"""Situação financeira do mês (saudável / ok / preocupante) e três maiores despesas."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site" / "py"))

from financas import health  # noqa: E402


def resumo(entradas=0, saidas=0, a_receber=0, a_pagar=0, aportes=0):
    return {"entradas": entradas, "saidas": saidas, "a_receber": a_receber, "a_pagar": a_pagar, "aportes": aportes}


class TestSaude(unittest.TestCase):
    def test_saudavel_quando_poupa_15_por_cento_ou_mais(self):
        r = health.assess(resumo(entradas=5000, saidas=3000), [])
        self.assertEqual(r["level"], "saudavel")
        self.assertAlmostEqual(r["taxa_poupanca"], 0.4)

    def test_aportes_contam_como_poupanca(self):
        r = health.assess(resumo(entradas=5000, saidas=4500, aportes=500), [])
        self.assertEqual(r["level"], "saudavel")

    def test_ok_quando_sobra_mas_abaixo_da_meta(self):
        r = health.assess(resumo(entradas=5000, saidas=4700), [])
        self.assertEqual(r["level"], "ok")
        self.assertIn("faltam", r["tip"])

    def test_preocupante_quando_gasto_passa_da_receita(self):
        r = health.assess(resumo(entradas=3000, saidas=3400), [{"category": "Lazer", "total": 1200}])
        self.assertEqual(r["level"], "preocupante")
        self.assertIn("Lazer", r["tip"])
        self.assertIn("120", r["tip"])  # 10% de 1.200

    def test_a_receber_e_a_pagar_entram_na_previsao(self):
        r = health.assess(resumo(entradas=2000, a_receber=3000, saidas=1000, a_pagar=2500), [])
        self.assertEqual(r["level"], "saudavel")  # receita 5000, gasto 3500, sobra 30%
        self.assertEqual(r["receita"], 5000)
        self.assertEqual(r["gasto"], 3500)

    def test_sem_receita_com_gasto_e_preocupante(self):
        self.assertEqual(health.assess(resumo(saidas=800), [])["level"], "preocupante")

    def test_sem_movimento(self):
        self.assertEqual(health.assess(resumo(), [])["level"], "sem_dados")

    def test_top3_ordenado_com_participacao(self):
        cats = [{"category": "A", "total": 500}, {"category": "B", "total": 300},
                {"category": "C", "total": 150}, {"category": "D", "total": 50}]
        top = health.top_spending(cats, 3)
        self.assertEqual([t["category"] for t in top], ["A", "B", "C"])
        self.assertAlmostEqual(top[0]["share"], 0.5)
        self.assertAlmostEqual(sum(t["share"] for t in top), 0.95)

    def test_top3_ignora_categorias_zeradas(self):
        self.assertEqual(health.top_spending([{"category": "A", "total": 0}], 3), [])



# ---------------------------------------------------------------- com retrato (perfil detectado pelo histórico)
def retrato(renda="estavel", base=5000.0, folga="folgado", neg=0, divida="sem_divida_cara", custo=0.0,
            compr=0.0, nivel="normal", meses=12):
    return {"ok": True, "meses": meses, "confianca": 1.0,
            "renda": {"estado": renda, "cv": 0.0, "media": base, "base": base},
            "folga": {"estado": folga, "taxa": 0.1, "meses_negativos": neg, "conta_negativa": False},
            "divida": {"estado": divida, "custo_mensal": custo, "comprometimento": compr, "nivel": nivel}}


class TestSaudeComRetrato(unittest.TestCase):
    def test_sem_retrato_ou_com_retrato_invalido_nada_muda(self):
        s = resumo(entradas=5000, saidas=3000)
        base = health.assess(s, [])
        self.assertEqual(health.assess(s, [], None), base)
        r = health.assess(s, [], {"ok": False, "meses": 2})
        self.assertEqual({k: r[k] for k in base}, base)
        self.assertNotIn("base_meses", r)

    def test_informa_em_quantos_meses_se_baseia(self):
        r = health.assess(resumo(entradas=5000, saidas=3000), [], retrato(meses=8))
        self.assertEqual(r["base_meses"], 8)

    def test_divida_cara_nunca_e_saudavel_e_manda_quitar_antes_de_investir(self):
        r = health.assess(resumo(entradas=5000, saidas=3000), [], retrato(divida="divida_cara", custo=420))
        self.assertEqual(r["level"], "ok")
        self.assertIn("420", r["tip"])
        self.assertIn("quit", r["tip"].lower())

    def test_comprometida_mostra_o_percentual_da_renda(self):
        r = health.assess(resumo(entradas=5000, saidas=3000), [],
                          retrato(divida="comprometida", custo=900, compr=0.42, nivel="alto"))
        self.assertIn("42%", r["tip"])

    def test_deficit_com_divida_cara_aponta_a_divida_como_prioridade(self):
        r = health.assess(resumo(entradas=3000, saidas=3400), [], retrato(divida="divida_cara", custo=500, folga="vermelho"))
        self.assertEqual(r["level"], "preocupante")
        self.assertIn("500", r["tip"])
        self.assertIn("400", r["tip"])  # ainda diz quanto falta

    def test_renda_irregular_mes_abaixo_da_base_fala_do_colchao(self):
        r = health.assess(resumo(entradas=1500, saidas=1800), [], retrato(renda="irregular", base=2200))
        self.assertEqual(r["level"], "preocupante")
        self.assertIn("renda-base", r["tip"])
        self.assertIn("colch", r["tip"])

    def test_renda_estavel_nao_recebe_texto_de_renda_base(self):
        r = health.assess(resumo(entradas=3000, saidas=3400), [], retrato(renda="estavel", base=5000))
        self.assertNotIn("renda-base", r["tip"])

    def test_mes_forte_de_renda_irregular_pede_separar_o_excedente(self):
        r = health.assess(resumo(entradas=9000, saidas=4000), [], retrato(renda="irregular", base=3000))
        self.assertEqual(r["level"], "saudavel")
        self.assertIn("excedente", r["tip"])

    def test_mes_normal_de_renda_estavel_mantem_a_dica_antiga(self):
        s = resumo(entradas=5000, saidas=3000)
        self.assertEqual(health.assess(s, [], retrato())["tip"], health.assess(s, [])["tip"])

    def test_apertado_tem_meta_de_5_por_cento(self):
        r = health.assess(resumo(entradas=5000, saidas=4900), [], retrato(folga="apertado"))
        self.assertEqual(r["level"], "ok")
        self.assertIn("5%", r["tip"])
        self.assertIn("150", r["tip"])  # 5% de 5000 = 250, já tem 100

    def test_apertado_que_ja_passou_de_5_aponta_o_proximo_passo(self):
        r = health.assess(resumo(entradas=5000, saidas=4600), [], retrato(folga="apertado"))
        self.assertIn("15%", r["tip"])

    def test_deficit_recorrente_cita_quantos_meses_fecharam_no_vermelho(self):
        r = health.assess(resumo(entradas=3000, saidas=3400), [], retrato(folga="vermelho", neg=4))
        self.assertIn("4 dos últimos 12", r["tip"])

    def test_mes_bom_com_historico_no_vermelho_nao_e_saudavel(self):
        r = health.assess(resumo(entradas=5000, saidas=3000), [], retrato(folga="vermelho", neg=4))
        self.assertEqual(r["level"], "ok")
        self.assertIn("recompor", r["tip"])

    def test_mes_sem_dados_continua_sem_dados(self):
        self.assertEqual(health.assess(resumo(), [], retrato())["level"], "sem_dados")


if __name__ == "__main__":
    unittest.main()
