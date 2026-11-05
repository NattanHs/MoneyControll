"""Consultor financeiro: recomendações priorizadas, cortes só em gastos não essenciais."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site" / "py"))

from financas import accounts, consultor, db, finance, perfil  # noqa: E402

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

    def meses(self, renda, gastos):
        for ym in DOZE:
            finance.save_transaction(self.conn, {"date": f"{ym}-02", "kind": "entrada", "amount": renda, "description": "Salário"})
            for cat, desc, valor in gastos:
                finance.save_transaction(self.conn, {"date": f"{ym}-10", "kind": "saida", "amount": valor,
                                                     "description": desc, "category": cat})


class TestConsultor(Base):
    def test_sem_historico(self):
        self.assertFalse(consultor.analisar(self.conn, TODAY)["ok"])

    def test_nunca_sugere_corte_em_essencial(self):
        self.meses(3000, [("Moradia", "Aluguel", 1400), ("Mercado", "Mercado", 600),
                          ("Saúde", "Plano de saúde", 400), ("Lazer", "Cinema", 700)])
        recs = consultor.analisar(self.conn, TODAY)["recomendacoes"]
        for r in recs:
            if r["tipo"] == "corte":
                self.assertIn(perfil.classificar(r["categoria"]), perfil.NAO_ESSENCIAIS)
                self.assertNotIn(r["categoria"], ("Moradia", "Mercado", "Saúde"))

    def test_corte_mostra_quanto_libera(self):
        self.meses(3000, [("Moradia", "Aluguel", 1000), ("Lazer", "Cinema", 800)])
        cortes = [r for r in consultor.analisar(self.conn, TODAY)["recomendacoes"] if r["tipo"] == "corte"]
        self.assertEqual(len(cortes), 1)
        self.assertEqual(cortes[0]["categoria"], "Lazer")
        self.assertAlmostEqual(cortes[0]["impacto"], 240, places=2)  # 30% de 800

    def test_delivery_entra_com_limite_proprio(self):
        self.meses(3000, [("Moradia", "Aluguel", 1000), ("Alimentação", "iFood", 300)])  # 10% > limite de 5%
        cortes = [r for r in consultor.analisar(self.conn, TODAY)["recomendacoes"] if r["tipo"] == "corte"]
        self.assertTrue(any(c["categoria"] == "Alimentação" for c in cortes))

    def test_sobra_negativa_vem_primeiro(self):
        self.meses(3000, [("Moradia", "Aluguel", 2000), ("Lazer", "Viagem", 1500)])
        recs = consultor.analisar(self.conn, TODAY)["recomendacoes"]
        self.assertEqual(recs[0]["prioridade"], 0)
        self.assertEqual(consultor.analisar(self.conn, TODAY)["nivel"], "preocupante")

    def test_divida_aparece_como_prioridade(self):
        self.meses(3000, [("Moradia", "Aluguel", 1000), ("", "Juros do rotativo", 200)])
        recs = consultor.analisar(self.conn, TODAY)["recomendacoes"]
        self.assertTrue(any(r["tipo"] == "divida" for r in recs))

    def test_textos_nao_expoem_perfil(self):
        self.meses(3000, [("Moradia", "Aluguel", 1000), ("Lazer", "Viagem", 900)])
        for r in consultor.analisar(self.conn, TODAY)["recomendacoes"]:
            for proibido in ("perfil", "responsável", "irresponsá", "organizad", "nota"):
                self.assertNotIn(proibido, (r["titulo"] + r["texto"]).lower())


if __name__ == "__main__":
    unittest.main()
