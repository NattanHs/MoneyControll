"""Relatórios e exportação."""
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site" / "py"))

from financas import accounts, analysis, cards, db, finance, xlsx  # noqa: E402

TODAY = "2026-10-19"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "f.db")
        db.init(self.conn)
        self.a = accounts.default_id(self.conn)
        self.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('opening_month', '2026-08')")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def tx(self, date, kind, amount, desc="x", cat="", acc=None, **extra):
        return finance.save_transaction(self.conn, {"date": date, "kind": kind, "amount": amount, "description": desc,
                                                     "category": cat, "account_id": acc, **extra})


class TestRelatorio(Base):
    def setUp(self):
        super().setUp()
        self.tx("2026-08-05", "entrada", 5000, "Salário")
        self.tx("2026-08-10", "saida", 800, "Mercado", "Mercado")
        self.tx("2026-09-05", "entrada", 5000, "Salário")
        self.tx("2026-09-12", "saida", 1200, "Mercado", "Mercado")
        self.tx("2026-09-20", "saida", 300, "Cinema", "Lazer")
        self.tx("2026-10-05", "entrada", 5000, "Salário")
        self.tx("2026-10-08", "saida", 1000, "Mercado", "Mercado")

    def test_totais_e_medias(self):
        r = analysis.report(self.conn, "2026-09", "2026-10", TODAY)
        self.assertEqual(r["totals"], {"entradas": 10000, "saidas": 2500, "aportes": 0, "resultado": 7500})
        self.assertEqual(r["average"]["saidas"], 1250)
        self.assertEqual([m["ym"] for m in r["months"]], ["2026-09", "2026-10"])

    def test_categorias_com_percentual_e_por_mes(self):
        r = analysis.report(self.conn, "2026-09", "2026-10", TODAY)
        top = r["categories"][0]
        self.assertEqual((top["category"], top["total"], top["by_month"]), ("Mercado", 2200, [1200, 1000]))
        self.assertAlmostEqual(sum(c["pct"] for c in r["categories"]), 100, delta=0.2)

    def test_periodo_anterior_e_variacao(self):
        r = analysis.report(self.conn, "2026-09", "2026-10", TODAY)
        self.assertEqual((r["previous"]["start"], r["previous"]["end"]), ("2026-07", "2026-08"))
        self.assertEqual(r["previous"]["totals"]["saidas"], 800)
        mercado = next(c for c in r["categories"] if c["category"] == "Mercado")
        self.assertEqual((mercado["prev_total"], mercado["delta"]), (800, 1400))

    def test_sem_periodo_anterior_quando_nao_ha_dados(self):
        r = analysis.report(self.conn, "2026-08", "2026-08", TODAY)
        self.assertIsNone(r["previous"])

    def test_validacoes(self):
        for s, e in (("2026-10", "2026-09"), ("x", "2026-09"), ("2020-01", "2026-09")):
            with self.assertRaises(ValueError):
                analysis.report(self.conn, s, e, TODAY)

    def test_transferencia_nao_entra_como_gasto_nem_receita(self):
        b = accounts.save_account(self.conn, {"name": "Poupança", "opening_balance": 0})
        accounts.save_transfer(self.conn, {"date": "2026-10-10", "from_account": self.a, "to_account": b, "amount": 700})
        r = analysis.report(self.conn, "2026-10", "2026-10", TODAY)
        self.assertEqual((r["totals"]["entradas"], r["totals"]["saidas"]), (5000, 1000))

    def test_por_conta_e_filtro(self):
        b = accounts.save_account(self.conn, {"name": "Poupança", "opening_balance": 0})
        self.tx("2026-10-15", "saida", 50, "Tarifa", "Banco", acc=b)
        r = analysis.report(self.conn, "2026-10", "2026-10", TODAY)
        self.assertEqual({x["name"]: x["saidas"] for x in r["accounts"]}, {"Conta principal": 1000, "Poupança": 50})
        rb = analysis.report(self.conn, "2026-10", "2026-10", TODAY, b)
        self.assertEqual((rb["totals"]["saidas"], rb["account_name"], rb["accounts"]), (50, "Poupança", []))

    def test_maiores_gastos_abrem_a_fatura(self):
        card = cards.save_card(self.conn, {"name": "Nubank", "closing_day": 10, "due_day": 17})
        self.tx("2026-10-02", "saida", 450, "TV", "Casa", card_id=card)
        r = analysis.report(self.conn, "2026-10", "2026-11", TODAY)
        descs = [t["description"] for t in r["top"]]
        self.assertIn("TV", descs)
        self.assertNotIn("Fatura Nubank", descs)
        self.assertEqual(r["top"][0]["amount"], max(t["amount"] for t in r["top"]))


class TestExportacao(Base):
    def setUp(self):
        super().setUp()
        self.tx("2026-10-05", "entrada", 5000, "Salário & cia <ok>")
        self.tx("2026-10-08", "saida", 1234.56, "Mercado", "Mercado")
        self.rep = analysis.report(self.conn, "2026-10", "2026-10", TODAY)

    def test_xlsx_valido_e_com_os_valores(self):
        data = analysis.to_xlsx(self.rep)
        z = zipfile.ZipFile(io.BytesIO(data))
        self.assertIsNone(z.testzip())
        for name in z.namelist():
            if name.endswith(".xml") or name.endswith(".rels"):
                ElementTree.fromstring(z.read(name))      # todo XML precisa ser bem formado
        wb = z.read("xl/workbook.xml").decode()
        for sheet in ("Resumo mensal", "Categorias", "Maiores gastos"):
            self.assertIn(sheet, wb)
        resumo = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("<v>1234.56</v>", resumo)
        self.assertIn("MoneyControl", resumo)

    def test_xlsx_abre_no_openpyxl_se_instalado(self):
        try:
            import openpyxl
        except ImportError:
            self.skipTest("openpyxl não instalado")
        wb = openpyxl.load_workbook(io.BytesIO(analysis.to_xlsx(self.rep)))
        ws = wb["Resumo mensal"]
        values = [c.value for row in ws.iter_rows() for c in row]
        self.assertIn(1234.56, values)
        self.assertIn("2026-10", values)

    def test_celulas_com_caracteres_especiais(self):
        data = xlsx.write([("A/B:C", [[("Total", "h"), ("2026-10-05", "date"), (0.25, "pct"), 'aspas " & <tag>']])])
        z = zipfile.ZipFile(io.BytesIO(data))
        ElementTree.fromstring(z.read("xl/worksheets/sheet1.xml"))
        self.assertIn("AB C".replace(" ", ""), z.read("xl/workbook.xml").decode())

    def test_csv_para_excel_brasileiro(self):
        raw = analysis.to_csv(self.rep)
        self.assertTrue(raw.startswith("﻿".encode("utf-8")))
        text = raw.decode("utf-8-sig")
        self.assertIn("1234,56", text)
        self.assertIn(";", text)


if __name__ == "__main__":
    unittest.main()
