"""Valor de uma despesa/receita fixa válido só num mês (sem alterar os outros meses)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site" / "py"))

from financas import accounts, db, finance  # noqa: E402

TODAY = "2026-10-19"


class TestFixaPorMes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "f.db")
        db.init(self.conn)
        accounts.default_id(self.conn)
        self.fid = finance.save_fixed(self.conn, {
            "kind": "saida", "description": "Internet", "category": "Casa", "amount": 100,
            "day": 10, "start_month": "2026-08", "end_month": "2026-12"})

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def valor(self, ym):
        data = finance.load_all(self.conn)
        itens = [i for i in finance.items_for_month(data, ym, TODAY) if i["source"] == "fixa"]
        self.assertEqual(len(itens), 1)
        return itens[0]["amount"]

    def test_mes_com_valor_proprio_so_altera_aquele_mes(self):
        finance.set_fixed_month(self.conn, self.fid, "2026-10", {"amount": 130})
        self.assertEqual(self.valor("2026-10"), 130)
        self.assertEqual(self.valor("2026-09"), 100)
        self.assertEqual(self.valor("2026-11"), 100)

    def test_voltar_ao_padrao_remove_so_o_mes(self):
        finance.set_fixed_month(self.conn, self.fid, "2026-10", {"amount": 130})
        finance.reset_fixed_month(self.conn, self.fid, "2026-10")
        self.assertEqual(self.valor("2026-10"), 100)

    def test_respeita_reajuste_vigente_nos_outros_meses(self):
        finance.save_fixed(self.conn, {"amount_from": "2026-10", "amount": 150, "kind": "saida",
                                       "description": "Internet", "category": "Casa", "day": 10,
                                       "start_month": "2026-08", "end_month": "2026-12"}, self.fid)
        self.assertEqual(self.valor("2026-09"), 100)
        self.assertEqual(self.valor("2026-10"), 150)
        finance.set_fixed_month(self.conn, self.fid, "2026-11", {"amount": 90})
        self.assertEqual(self.valor("2026-11"), 90)
        self.assertEqual(self.valor("2026-12"), 150)

    def test_item_marca_quando_o_mes_tem_valor_proprio(self):
        finance.set_fixed_month(self.conn, self.fid, "2026-10", {"amount": 130})
        itens = {i["tag"] or "": i for i in finance.items_for_month(finance.load_all(self.conn), "2026-10", TODAY)
                 if i["source"] == "fixa"}
        self.assertTrue(list(itens.values())[0]["month_override"])
        itens = [i for i in finance.items_for_month(finance.load_all(self.conn), "2026-09", TODAY) if i["source"] == "fixa"]
        self.assertFalse(itens[0]["month_override"])

    def test_rejeita_mes_fora_do_periodo(self):
        with self.assertRaises(ValueError):
            finance.set_fixed_month(self.conn, self.fid, "2026-07", {"amount": 50})
        with self.assertRaises(ValueError):
            finance.set_fixed_month(self.conn, self.fid, "2027-01", {"amount": 50})

    def test_rejeita_valor_invalido(self):
        for bad in (0, -5, "abc"):
            with self.assertRaises(ValueError):
                finance.set_fixed_month(self.conn, self.fid, "2026-10", {"amount": bad})

    def test_excluir_fixa_apaga_sobreposicoes(self):
        finance.set_fixed_month(self.conn, self.fid, "2026-10", {"amount": 130})
        finance.delete_fixed(self.conn, self.fid)
        n = self.conn.execute("SELECT COUNT(*) FROM month_overrides").fetchone()[0]
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
