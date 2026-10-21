"""Regras de segurança da sincronização: dados novos nunca são trocados por dados antigos, e o que foi lançado não se perde.

Cada "aparelho" é uma pasta de dados própria; `Device.use()` aponta a API para ele (a API guarda um banco por vez).
Os arquivos trocados entre eles (Drive, backups) são os mesmos bytes que o app envia e baixa de verdade.
"""
import base64
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site" / "py"))

from financas import api, syncmeta  # noqa: E402

TODAY = "2026-10-21"


class Device:
    def __init__(self, root: Path, name: str):
        self.db = root / name / "financas.db"
        api.start(str(self.db))
        api._state["backup_day"] = None

    def use(self):
        api.start(str(self.db))
        api._state["backup_day"] = None
        return self

    def call(self, method, path, body=None):
        self.use()
        return json.loads(api.dispatch(method, path, None if body is None else json.dumps(body), TODAY))

    def ok(self, method, path, body=None):
        r = self.call(method, path, body)
        assert r["status"] == 200, r
        return r["body"]

    def err(self, method, path, body=None):
        r = self.call(method, path, body)
        assert r["status"] == 400, r
        return r["body"]["error"]

    def add(self, desc, day="2026-10-05"):
        return self.ok("POST", "/api/transactions", {"date": day, "kind": "saida", "description": desc, "category": "Mercado", "amount": 10})

    def descs(self):
        con = sqlite3.connect(self.db)
        try:
            return sorted(r[0] for r in con.execute("SELECT description FROM transactions"))
        finally:
            con.close()

    def state(self):
        return self.ok("GET", "/api/sync/state")

    def stamp(self, **kw):
        return self.ok("POST", "/api/sync/stamp", kw)

    def export(self) -> bytes:
        r = self.call("GET", "/api/backup")
        return Path(r["file"]["path"]).read_bytes()


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


class SyncBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.a = Device(self.root, "a")
        self.b = Device(self.root, "b")

    def tearDown(self):
        self.tmp.cleanup()

    def adopt(self, dev, raw, force=False, expect="auto"):
        expect_head = dev.state()["head"] if expect == "auto" else expect
        return dev.call("POST", "/api/sync/adopt", {"data": b64(raw), "expect_head": expect_head, "force": force})


class TestRelacao(unittest.TestCase):
    def test_matriz(self):
        r = syncmeta.relation
        self.assertEqual(r(["a"], ["a"]), "same")
        self.assertEqual(r(["a", "b"], ["a"]), "file-older")        # arquivo é ancestral: mais antigo
        self.assertEqual(r(["a"], ["a", "b"]), "file-newer")        # aqui é ancestral do arquivo: arquivo mais novo
        self.assertEqual(r(["a", "b"], ["a", "c"]), "diverged")     # cada um seguiu um caminho
        self.assertEqual(r([], ["a"]), "unknown")                   # banco antigo, sem linhagem
        self.assertEqual(r(["a"], []), "unknown")
        self.assertEqual(r([], []), "unknown")

    def test_relogio_nao_entra_na_conta(self):
        # a relação só depende das listas de versões; nenhuma data é consultada
        self.assertEqual(syncmeta.relation(["x", "y"], ["x"]), "file-older")


class TestMarcacao(SyncBase):
    def test_gravar_marca_pendente_e_ler_nao(self):
        self.assertFalse(self.a.state()["dirty"])
        self.a.ok("GET", "/api/settings")
        self.assertFalse(self.a.state()["dirty"])
        self.a.add("Mercado")
        self.assertTrue(self.a.state()["dirty"])

    def test_criar_copia_de_seguranca_nao_conta_como_alteracao(self):
        self.a.add("x")
        self.a.stamp()
        self.a.ok("POST", "/api/backups")
        self.assertFalse(self.a.state()["dirty"])

    def test_stamp_cria_versao_so_quando_ha_pendencia(self):
        self.a.add("x")
        s1 = self.a.stamp()
        self.assertTrue(s1["stamped"])
        self.assertFalse(s1["dirty"])
        s2 = self.a.stamp()
        self.assertFalse(s2["stamped"])
        self.assertEqual(s1["head"], s2["head"])
        self.a.add("y")
        s3 = self.a.stamp()
        self.assertTrue(s3["stamped"])
        self.assertEqual(s3["history"][:-1], s1["history"])        # a nova versão descende da anterior

    def test_o_arquivo_exportado_leva_a_linhagem(self):
        self.a.add("x")
        head = self.a.stamp()["head"]
        raw = self.a.export()
        info = self.b.ok("POST", "/api/backups/inspect-upload", {"data": b64(raw)})
        self.assertEqual(info["file"]["head"], head)
        self.assertEqual(info["file"]["tx_count"], 1)
        self.assertEqual(info["file"]["last_date"], "2026-10-05")


class TestAdotar(SyncBase):
    def test_aparelho_vazio_recebe_o_do_drive(self):
        self.a.add("do celular")
        self.a.stamp()
        r = self.adopt(self.b, self.a.export())
        self.assertEqual(r["status"], 200, r)
        self.assertEqual(self.b.descs(), ["do celular"])
        st = self.b.state()
        self.assertEqual(st["head"], self.a.state()["head"])
        self.assertFalse(st["dirty"])

    def test_versao_mais_nova_substitui(self):
        self.a.add("um")
        self.a.stamp()
        self.adopt(self.b, self.a.export())
        self.b.add("dois")
        self.b.stamp()
        r = self.adopt(self.a, self.b.export())
        self.assertEqual(r["status"], 200, r)
        self.assertEqual(self.a.descs(), ["dois", "um"])

    def test_arquivo_mais_antigo_nunca_substitui(self):
        self.a.add("um")
        self.a.stamp()
        velho = self.a.export()
        self.adopt(self.b, velho)
        self.b.add("dois")
        self.b.stamp()
        r = self.adopt(self.b, velho)                       # tenta trazer a versão antiga para quem já está à frente
        self.assertEqual(r["status"], 400)
        self.assertIn("não é mais novo", r["body"]["error"])
        self.assertEqual(self.b.descs(), ["dois", "um"])    # nada se perdeu

    def test_linhagens_diferentes_nunca_substituem(self):
        self.a.add("um")
        self.a.stamp()
        self.adopt(self.b, self.a.export())
        self.a.add("so no A")
        self.a.stamp()
        self.b.add("so no B")
        self.b.stamp()
        r = self.adopt(self.a, self.b.export())
        self.assertEqual(r["status"], 400)
        self.assertEqual(self.a.descs(), ["so no A", "um"])

    def test_alteracoes_pendentes_nunca_sao_apagadas(self):
        self.a.add("um")
        self.a.stamp()
        self.adopt(self.b, self.a.export())
        self.a.add("dois")
        self.a.stamp()
        self.b.add("lancado agora no B")                    # pendente, ainda sem versão
        r = self.adopt(self.b, self.a.export())
        self.assertEqual(r["status"], 400)
        self.assertIn("mudaram durante a sincronização", r["body"]["error"])
        self.assertIn("lancado agora no B", self.b.descs())

    def test_aparelho_recem_configurado_sem_lancamentos_recebe_o_drive(self):
        self.a.add("do celular")
        self.a.stamp()
        self.b.ok("PUT", "/api/settings", {"opening_balance": 100, "opening_month": "2026-10", "cdi_annual": 10})
        self.assertTrue(self.b.state()["dirty"])            # só configuração, sem lançamentos
        self.assertEqual(self.adopt(self.b, self.a.export())["status"], 200)
        self.assertEqual(self.b.descs(), ["do celular"])

    def test_dados_mudaram_enquanto_baixava(self):
        self.a.add("um")
        self.a.stamp()
        self.adopt(self.b, self.a.export())
        self.a.add("dois")
        self.a.stamp()
        cabeca_vista = self.b.state()["head"]
        self.b.add("lancado durante o download")
        self.b.stamp()                                      # outra versão aqui depois de a sincronização começar
        r = self.adopt(self.b, self.a.export(), expect=cabeca_vista)
        self.assertEqual(r["status"], 400)
        self.assertIn("lancado durante o download", self.b.descs())

    def test_forcar_so_com_escolha_da_pessoa_e_guarda_copia(self):
        self.a.add("um")
        self.a.stamp()
        self.adopt(self.b, self.a.export())
        self.a.add("so no A")
        self.a.stamp()
        self.b.add("so no B")
        r = self.adopt(self.b, self.a.export(), force=True)
        self.assertEqual(r["status"], 200, r)
        self.assertEqual(self.b.descs(), ["so no A", "um"])
        guardadas = []
        for p in (self.b.db.parent / "backups").glob("antes-*.db"):
            con = sqlite3.connect(p)
            try:
                guardadas += [x[0] for x in con.execute("SELECT description FROM transactions")]
            finally:
                con.close()
        self.assertIn("so no B", guardadas, "a cópia de segurança do que existia deveria ter sido guardada")

    def test_banco_antigo_sem_linhagem(self):
        # um arquivo de banco criado antes da sincronização existir (sem a tabela sync_meta)
        self.a.add("antigo")
        raw = self.a.export()
        legacy = self.root / "legacy.db"
        legacy.write_bytes(raw)
        con = sqlite3.connect(legacy)
        con.execute("DROP TABLE sync_meta")
        con.commit()
        con.close()
        raw = legacy.read_bytes()
        info = self.b.ok("POST", "/api/backups/inspect-upload", {"data": b64(raw)})
        self.assertEqual(info["relation"], "unknown")
        self.assertEqual(self.adopt(self.b, raw)["status"], 200)       # aqui não há dado nenhum: pode receber
        self.assertEqual(self.b.descs(), ["antigo"])
        outro = Device(self.root, "c")
        outro.add("dado proprio")
        r = self.adopt(outro, raw)                                      # aqui há dados e não dá para saber: recusa
        self.assertEqual(r["status"], 400)
        self.assertEqual(outro.descs(), ["dado proprio"])


class TestManterOsDaqui(SyncBase):
    def test_nova_versao_descende_do_drive(self):
        self.a.add("um")
        self.a.stamp()
        self.adopt(self.b, self.a.export())
        self.a.add("so no A")
        self.a.stamp()
        self.b.add("so no B")
        b_drive = self.b.stamp()
        a_state = self.a.state()
        cmp = self.a.ok("POST", "/api/backups/inspect-upload", {"data": b64(self.b.export())})
        self.assertEqual(cmp["relation"], "diverged")
        # a pessoa escolhe "manter os daqui" no A: a nova versão do A absorve a linhagem do B
        mine = self.a.stamp(absorb=cmp["file"]["history"])
        self.assertTrue(mine["stamped"])
        self.assertIn(b_drive["head"], mine["history"])
        self.assertIn(a_state["head"], mine["history"])
        # vista do B, a versão do A agora é uma atualização legítima (mais nova)
        back = self.b.ok("POST", "/api/backups/inspect-upload", {"data": b64(self.a.export())})
        self.assertEqual(back["relation"], "file-newer")


class TestRestauracaoManual(SyncBase):
    def test_arquivo_mais_antigo_e_avisado(self):
        self.a.add("um")
        self.a.stamp()
        velho = self.a.export()
        self.a.add("dois")
        self.a.stamp()
        info = self.a.ok("POST", "/api/backups/inspect-upload", {"data": b64(velho)})
        self.assertEqual(info["relation"], "file-older")
        self.assertEqual(info["file"]["tx_count"], 1)
        self.assertEqual(info["local"]["tx_count"], 2)

    def test_restaurar_antigo_vira_alteracao_deliberada_por_cima_da_atual(self):
        self.a.add("um")
        self.a.stamp()
        velho = self.a.export()
        self.a.add("dois")
        antes = self.a.stamp()
        r = self.a.ok("POST", "/api/backups/restore-upload", {"data": b64(velho)})
        self.assertEqual(r["relation"], "file-older")
        self.assertEqual(self.a.descs(), ["um"])
        st = self.a.state()
        self.assertTrue(st["dirty"])
        self.assertTrue(st["rewind"])                       # o Drive vai guardar uma cópia antes de ser substituído
        self.assertIn(antes["head"], st["history"])         # continua descendo da versão que existia: o Drive aceita
        self.assertNotEqual(st["head"], antes["head"])

    def test_restaurar_arquivo_mais_novo_assume_a_linhagem(self):
        self.a.add("um")
        self.a.stamp()
        self.adopt(self.b, self.a.export())
        self.a.add("dois")
        self.a.stamp()
        r = self.b.ok("POST", "/api/backups/restore-upload", {"data": b64(self.a.export())})
        self.assertEqual(r["relation"], "file-newer")
        st = self.b.state()
        self.assertFalse(st["dirty"])
        self.assertEqual(st["head"], self.a.state()["head"])

    def test_restaurar_pela_lista_do_app(self):
        self.a.add("um")
        self.a.stamp()
        nome = self.a.ok("POST", "/api/backups")["name"]
        self.a.add("dois")
        self.a.stamp()
        info = self.a.ok("POST", "/api/backups/inspect", {"name": nome})
        self.assertEqual(info["relation"], "file-older")


class TestReset(SyncBase):
    def test_reset_preserva_a_linhagem_e_marca_pendente(self):
        self.a.add("um")
        antes = self.a.stamp()
        self.a.ok("POST", "/api/reset", {"confirm": "RESETAR"})
        st = self.a.state()
        self.assertEqual(st["history"], antes["history"])
        self.assertTrue(st["dirty"])
        self.assertTrue(st["rewind"])
        self.assertEqual(self.a.descs(), [])


if __name__ == "__main__":
    unittest.main()
