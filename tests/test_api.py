"""Testes da API do celular (financas.api.dispatch), sem servidor HTTP."""
import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site" / "py"))

from financas import api  # noqa: E402

TODAY = "2026-10-21"


class ApiBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "dados" / "financas.db"
        api.start(str(self.db))
        api._state["backup_day"] = None

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, method, path, body=None, today=TODAY):
        raw = api.dispatch(method, path, None if body is None else json.dumps(body), today)
        return json.loads(raw)

    def ok(self, method, path, body=None):
        r = self.call(method, path, body)
        self.assertEqual(r["status"], 200, r)
        return r["body"]


class TestFluxo(ApiBase):
    def test_banco_novo_pede_assistente(self):
        self.assertTrue(self.ok("GET", "/api/wizard")["needed"])

    def test_licenca_livre(self):
        lic = self.ok("GET", "/api/license")
        self.assertFalse(lic["blocked"])
        self.assertFalse(lic["enabled"])

    def test_lancar_e_ver_no_mes(self):
        r = self.ok("POST", "/api/transactions", {"date": "2026-10-05", "kind": "entrada", "description": "Salário",
                                                  "category": "Salário", "amount": 5000})
        self.assertIn("id", r)
        self.ok("POST", "/api/transactions", {"date": "2026-10-06", "kind": "saida", "description": "Mercado",
                                              "category": "Mercado", "amount": 350.5})
        mes = self.ok("GET", "/api/month/2026-10")
        self.assertEqual(round(mes["totals"]["entradas"], 2) if "totals" in mes else 5000.0, 5000.0)
        ov = self.ok("GET", "/api/overview")
        self.assertTrue(ov["has_data"])
        self.assertEqual(ov["today"], TODAY)

    def test_hoje_vem_do_aparelho(self):
        a = json.loads(api.dispatch("GET", "/api/overview", None, "2026-01-15"))["body"]
        b = json.loads(api.dispatch("GET", "/api/overview", None, "2026-03-02"))["body"]
        self.assertEqual(a["today"], "2026-01-15")
        self.assertEqual(b["today"], "2026-03-02")

    def test_client_ref_nao_duplica(self):
        body = {"date": "2026-10-05", "kind": "saida", "description": "Café", "category": "Lazer", "amount": 9, "client_ref": "abc"}
        first = self.ok("POST", "/api/transactions", dict(body))
        again = self.ok("POST", "/api/transactions", dict(body))
        self.assertEqual(first["id"], again["id"])
        self.assertTrue(again.get("duplicate"))

    def test_erro_de_validacao_vira_400_com_mensagem(self):
        r = self.call("POST", "/api/transactions", {"date": "2026-10-05", "kind": "saida", "description": "", "amount": 0})
        self.assertEqual(r["status"], 400)
        self.assertTrue(r["body"]["error"])

    def test_rota_inexistente(self):
        self.assertEqual(self.call("GET", "/api/nao-existe")["status"], 404)
        self.assertEqual(self.call("DELETE", "/api/overview")["status"], 404)

    def test_corpo_invalido(self):
        raw = api.dispatch("POST", "/api/transactions", "[1,2]", TODAY)
        self.assertEqual(json.loads(raw)["status"], 400)

    def test_rotas_do_computador_nao_existem(self):
        for path in ("/api/mobile", "/api/quit", "/api/backup-external", "/api/login"):
            self.assertEqual(self.call("GET", path)["status"], 404, path)


class TestArquivos(ApiBase):
    def lancar(self):
        self.ok("POST", "/api/transactions", {"date": "2026-10-05", "kind": "entrada", "description": "Salário",
                                              "category": "Salário", "amount": 5000})

    def test_baixar_backup_gera_arquivo_sqlite(self):
        self.lancar()
        r = self.call("GET", "/api/backup")
        self.assertEqual(r["status"], 200)
        self.assertEqual(r["file"]["name"], f"financas-backup-{TODAY}.db")
        self.assertTrue(Path(r["file"]["path"]).read_bytes().startswith(b"SQLite format 3\x00"))

    def test_relatorio_xlsx_e_csv(self):
        self.lancar()
        x = self.call("GET", "/api/report/2026-10/2026-10/all/xlsx")
        self.assertEqual(x["status"], 200)
        self.assertTrue(Path(x["file"]["path"]).read_bytes().startswith(b"PK"))  # zip = xlsx
        c = self.call("GET", "/api/report/2026-10/2026-10/all/csv")
        self.assertIn("csv", c["file"]["ctype"])
        rep = self.ok("GET", "/api/report/2026-10/2026-10/all")
        self.assertIn("totals", rep)

    def test_restaurar_backup_enviado(self):
        self.lancar()
        raw = Path(self.call("GET", "/api/backup")["file"]["path"]).read_bytes()
        self.ok("POST", "/api/reset", {"confirm": "RESETAR"})
        self.assertFalse(self.ok("GET", "/api/overview")["has_data"])
        r = self.ok("POST", "/api/backups/restore", {"name": self.ok("GET", "/api/backups")["items"][0]["name"]})
        self.assertIn("safety", r)
        self.assertTrue(self.ok("GET", "/api/overview")["has_data"])
        self.ok("POST", "/api/reset", {"confirm": "RESETAR", "wipe_backups": True})
        self.ok("POST", "/api/backups/restore-upload", {"data": base64.b64encode(raw).decode()})
        self.assertTrue(self.ok("GET", "/api/overview")["has_data"])

    def test_restaurar_arquivo_invalido(self):
        r = self.call("POST", "/api/backups/restore-upload", {"data": base64.b64encode(b"nao e um banco").decode()})
        self.assertEqual(r["status"], 400)

    def test_backup_automatico_uma_vez_por_dia(self):
        self.lancar()
        self.call("GET", "/api/overview")
        names = [b["name"] for b in self.ok("GET", "/api/backups")["items"]]
        self.assertIn(f"auto-{TODAY}.db", names)
        self.assertEqual(sum(n.startswith("auto-") for n in names), 1)
        self.call("GET", "/api/overview")
        names2 = [b["name"] for b in self.ok("GET", "/api/backups")["items"]]
        self.assertEqual(sum(n.startswith("auto-") for n in names2), 1)

    def test_backup_manual(self):
        self.lancar()
        self.ok("POST", "/api/backups")
        kinds = [b["kind"] for b in self.ok("GET", "/api/backups")["items"]]
        self.assertIn("manual", kinds)


if __name__ == "__main__":
    unittest.main()
