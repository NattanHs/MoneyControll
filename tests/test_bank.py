"""Testes da importação de extratos OFX e da conciliação."""
import base64
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site" / "py"))

from financas import bank, cards, db, finance, ofx  # noqa: E402

TODAY = "2026-10-19"


def make_ofx(txns, kind="conta", acct="12345-6", ledger=None, sgml=True, encoding="cp1252"):
    """txns: [(data AAAAMMDD, valor, fitid, memo)]."""
    rows = "".join(
        f"<STMTTRN><TRNTYPE>{'CREDIT' if float(a) > 0 else 'DEBIT'}<DTPOSTED>{d}000000[-3:BRT]<TRNAMT>{a}<FITID>{f}<MEMO>{m}</STMTTRN>\n"
        for d, a, f, m in txns)
    bal = f"<LEDGERBAL><BALAMT>{ledger[1]}<DTASOF>{ledger[0]}</LEDGERBAL>" if ledger else ""
    if kind == "conta":
        body = (f"<BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>BRL<BANKACCTFROM><BANKID>0341<ACCTID>{acct}<ACCTTYPE>CHECKING</BANKACCTFROM>"
                f"<BANKTRANLIST><DTSTART>20261001<DTEND>20261018\n{rows}</BANKTRANLIST>{bal}</STMTRS></STMTTRNRS></BANKMSGSRSV1>")
    else:
        body = (f"<CREDITCARDMSGSRSV1><CCSTMTTRNRS><CCSTMTRS><CURDEF>BRL<CCACCTFROM><ACCTID>{acct}</CCACCTFROM>"
                f"<BANKTRANLIST><DTSTART>20261001<DTEND>20261018\n{rows}</BANKTRANLIST>{bal}</CCSTMTRS></CCSTMTTRNRS></CREDITCARDMSGSRSV1>")
    text = "OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nENCODING:USASCII\nCHARSET:1252\n\n<OFX>" + body + "</OFX>"
    if not sgml:  # XML: com tags de fechamento
        import re
        text = re.sub(r"(<([A-Z]+)>)([^<\n]+)(?=<)", lambda m: f"{m.group(1)}{m.group(3)}</{m.group(2)}>", text)
    return text.encode(encoding)


def b64(raw):
    return base64.b64encode(raw).decode()


class TestParser(unittest.TestCase):
    def test_sgml_e_valores(self):
        st = ofx.parse(make_ofx([("20261010", "-1800.00", "A1", "PIX ALUGUEL"), ("20261005", "6500.5", "A2", "SALARIO")],
                                ledger=("20261018", "4700.50")))
        self.assertEqual((st["kind"], st["account_key"], st["period_start"], st["period_end"]),
                         ("conta", "conta:0341:12345-6", "2026-10-01", "2026-10-18"))
        self.assertEqual([(t["date"], t["amount"]) for t in st["txns"]], [("2026-10-10", -1800.0), ("2026-10-05", 6500.5)])
        self.assertEqual((st["ledger_balance"], st["ledger_date"]), (4700.5, "2026-10-18"))

    def test_xml_cartao_e_acentos(self):
        st = ofx.parse(make_ofx([("20261003", "-89.90", "C1", "IFOOD *RESTAURANTE AÇAÍ")], kind="cartao", acct="4111111111111234", sgml=False))
        self.assertEqual((st["kind"], st["account_label"]), ("cartao", "Cartão final 1234"))
        self.assertIn("AÇAÍ", st["txns"][0]["memo"])
        self.assertEqual(st["txns"][0]["amount"], -89.9)

    def test_utf8_e_bom(self):
        raw = b"\xef\xbb\xbf" + make_ofx([("20261003", "-10.00", "U1", "CAFÉ")], encoding="utf-8")
        self.assertIn("CAFÉ", ofx.parse(raw)["txns"][0]["memo"])

    def test_formatos_de_valor_e_data(self):
        self.assertEqual([ofx.parse_amount(x) for x in ("-1.234,56", "1234,5", "-0.99", "10")], [-1234.56, 1234.5, -0.99, 10.0])
        self.assertEqual(ofx.parse_date("20261019120000[-3:BRT]"), "2026-10-19")
        with self.assertRaises(ValueError):
            ofx.parse_date("20261340")

    def test_fitid_ausente_ou_repetido(self):
        raw = make_ofx([("20261010", "-10.00", "X", "A"), ("20261010", "-10.00", "X", "A")])
        st = ofx.parse(raw)
        self.assertEqual(len({t["fitid"] for t in st["txns"]}), 2)
        raw2 = raw.replace(b"<FITID>X", b"<FITID>")
        ids = [t["fitid"] for t in ofx.parse(raw2)["txns"]]
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(ids, [t["fitid"] for t in ofx.parse(raw2)["txns"]])  # estável entre leituras

    def test_arquivos_invalidos(self):
        for raw in (b"", b"nao e ofx", b"<OFX></OFX>", make_ofx([]),
                    make_ofx([("20261010", "-1", "1", "a")]) + b"<STMTRS><BANKTRANLIST><STMTTRN><DTPOSTED>20261010<TRNAMT>1<FITID>2</STMTTRN></BANKTRANLIST></STMTRS>"):
            with self.assertRaises(ValueError):
                ofx.parse(raw)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.init(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def tx(self, date, kind, amount, desc="x", cat="", **kw):
        return finance.save_transaction(self.conn, {"date": date, "kind": kind, "amount": amount, "description": desc,
                                                     "category": cat, **kw})

    def imp(self, raw, **kw):
        return bank.import_ofx(self.conn, {"data": b64(raw), "filename": "e.ofx", **kw})

    def rows(self, view="pendente"):
        return bank.overview(self.conn, view, TODAY)["rows"]

    def status(self):
        return {r["memo"]: r["status"] for r in self.rows("todas")}


class TestImportacao(Base):
    def test_importa_e_nao_duplica(self):
        raw = make_ofx([("20261010", "-10.00", "A", "um"), ("20261011", "-20.00", "B", "dois")])
        pv = bank.preview(self.conn, {"data": b64(raw)})
        self.assertEqual((pv["total"], pv["new"], pv["duplicated"], pv["outflow"]), (2, 2, 0, 30.0))
        self.assertEqual(self.imp(raw)["new"], 2)
        r = self.imp(raw)  # de novo
        self.assertEqual((r["new"], r["duplicated"], r["import_id"]), (0, 2, None))
        self.assertEqual(len(self.rows("todas")), 2)
        raw2 = make_ofx([("20261011", "-20.00", "B", "dois"), ("20261012", "-5.00", "C", "tres")])  # sobreposto
        r = self.imp(raw2)
        self.assertEqual((r["new"], r["duplicated"]), (1, 1))

    def test_contas_diferentes_com_mesmo_fitid(self):
        self.imp(make_ofx([("20261010", "-10.00", "A", "um")], acct="111"))
        self.imp(make_ofx([("20261010", "-10.00", "A", "um")], acct="222"))
        self.assertEqual(len(self.rows("todas")), 2)

    def test_cartao_exige_cartao_e_ignora_pagamentos(self):
        raw = make_ofx([("20261003", "-50.00", "C1", "LOJA"), ("20261005", "300.00", "C2", "PAGAMENTO")], kind="cartao")
        with self.assertRaises(ValueError):
            self.imp(raw)
        with self.assertRaises(ValueError):
            self.imp(raw, card_id=99)
        cid = cards.save_card(self.conn, {"name": "Nubank", "closing_day": 10, "due_day": 17})
        r = self.imp(raw, card_id=cid)
        self.assertEqual((r["new"], r["auto_ignored"]), (2, 1))
        self.assertEqual(self.status(), {"LOJA": "pendente", "PAGAMENTO": "ignorada"})

    def test_arquivo_invalido(self):
        for bad in ("###", b64(b"nada")):
            with self.assertRaises(ValueError):
                bank.preview(self.conn, {"data": bad})


class TestConciliacao(Base):
    def setUp(self):
        super().setUp()
        finance.save_fixed(self.conn, {"kind": "saida", "description": "Aluguel", "amount": 1800, "day": 20, "start_month": "2026-01"})
        self.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('opening_month', '2026-10')")

    def test_sugere_e_concilia_marcando_como_paga(self):
        raw = make_ofx([("20261018", "-1800.00", "A1", "PIX ALUGUEL")])
        self.imp(raw)
        row = self.rows()[0]
        self.assertEqual((row["suggestion"]["description"], row["suggestion"]["days"]), ("Aluguel", 2))
        self.assertEqual(finance.month_view(self.conn, "2026-10", TODAY)["summary"]["a_pagar"], 1800)  # vence dia 20
        bank.reconcile(self.conn, row["id"], row["suggestion"], TODAY)
        v = finance.month_view(self.conn, "2026-10", TODAY)
        self.assertEqual((v["summary"]["a_pagar"], v["summary"]["saidas"]), (0, 1800))
        self.assertTrue(next(i for i in v["items"] if i["description"] == "Aluguel")["reconciled"])
        self.assertEqual(self.status(), {"PIX ALUGUEL": "conciliada"})

    def test_desfazer_volta_ao_estado_anterior(self):
        self.imp(make_ofx([("20261018", "-1800.00", "A1", "PIX ALUGUEL")]))
        row = self.rows()[0]
        bank.reconcile(self.conn, row["id"], row["suggestion"], TODAY)
        bank.unreconcile(self.conn, row["id"], TODAY)
        self.assertEqual(finance.month_view(self.conn, "2026-10", TODAY)["summary"]["a_pagar"], 1800)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM payment_marks").fetchone()[0], 0)
        self.assertEqual(self.rows()[0]["status"], "pendente")

    def test_mesmo_valor_duas_linhas_vence_a_mais_proxima(self):
        a = self.tx("2026-10-10", "saida", 100, "Farmácia")
        self.imp(make_ofx([("20261009", "-100.00", "X1", "COMPRA 1"), ("20261016", "-100.00", "X2", "COMPRA 2")]))
        sug = {r["memo"]: r["suggestion"] for r in self.rows()}
        self.assertEqual(sug["COMPRA 1"]["ref_id"], a)
        self.assertIsNone(sug["COMPRA 2"])  # o lançamento já foi usado pela linha mais próxima

    def test_fora_da_janela_ou_sentido_errado_nao_sugere(self):
        self.tx("2026-10-01", "saida", 100, "A")
        self.tx("2026-10-12", "entrada", 100, "B")
        self.imp(make_ofx([("20261012", "-100.00", "1", "X"), ("20261001", "100.00", "2", "Y")]))
        for r in self.rows():
            if r["memo"] == "X":
                self.assertIsNone(r["suggestion"])  # entrada B tem o mesmo valor mas sentido oposto; saída A está a 11 dias
            else:
                self.assertIsNone(r["suggestion"])

    def test_lancar_a_partir_do_extrato_e_aprender_categoria(self):
        self.imp(make_ofx([("20261005", "-45.00", "1", "PADARIA CENTRAL 123"), ("20261012", "-30.00", "2", "PADARIA CENTRAL 456")]))
        first = next(r for r in self.rows() if "123" in r["memo"])
        bank.create_from(self.conn, first["id"], {"category": "Alimentação"}, TODAY)
        t = self.conn.execute("SELECT * FROM transactions").fetchone()
        self.assertEqual((t["kind"], t["amount"], t["date"], t["category"]), ("saida", 45.0, "2026-10-05", "Alimentação"))
        second = next(r for r in self.rows() if "456" in r["memo"])
        self.assertEqual(second["suggested_category"], "Alimentação")  # aprendeu pelo nome (sem números)
        bank.create_all(self.conn, TODAY)
        self.assertEqual(self.conn.execute("SELECT category FROM transactions WHERE amount = 30").fetchone()[0], "Alimentação")
        self.assertEqual(self.rows(), [])
        bank.unreconcile(self.conn, first["id"], TODAY)  # desfazer um lançado remove o lançamento criado
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM transactions WHERE amount = 45").fetchone()[0], 0)

    def test_entrada_lancada_com_data_futura_vira_data_do_banco(self):
        tid = self.tx("2026-10-25", "entrada", 900, "Freela")
        self.imp(make_ofx([("20261017", "900.00", "F1", "TED RECEBIDA")]))
        row = self.rows()[0]
        self.assertIsNone(row["suggestion"])  # 8 dias de diferença: fora da janela, vincular à mão
        c = bank.candidates(self.conn, row["id"], TODAY)
        self.assertEqual((c[0]["description"], c[0]["exact"]), ("Freela", True))
        res = bank.reconcile(self.conn, row["id"], c[0], TODAY)
        self.assertIn("data ajustada para a do banco", res["notes"])
        self.assertEqual(self.conn.execute("SELECT date FROM transactions WHERE id = ?", (tid,)).fetchone()[0], "2026-10-17")
        self.assertEqual(finance.month_view(self.conn, "2026-10", TODAY)["summary"]["entradas"], 900)

    def test_vincular_com_valor_diferente_ajustando(self):
        tid = self.tx("2026-10-10", "saida", 100, "Luz")
        self.imp(make_ofx([("20261010", "-98.50", "L1", "CPFL")]))
        row = self.rows()[0]
        self.assertIsNone(row["suggestion"])
        c = next(x for x in bank.candidates(self.conn, row["id"], TODAY) if x["ref_id"] == tid)
        self.assertEqual((c["exact"], c["diff"]), (False, 1.5))
        bank.reconcile(self.conn, row["id"], {**c, "fix_amount": True}, TODAY)
        self.assertEqual(self.conn.execute("SELECT amount FROM transactions WHERE id = ?", (tid,)).fetchone()[0], 98.5)

    def test_nao_concilia_duas_vezes_nem_sentido_oposto(self):
        a = self.tx("2026-10-10", "saida", 100, "A")
        self.imp(make_ofx([("20261010", "-100.00", "1", "X"), ("20261010", "-100.00", "2", "Y")]))
        r1, r2 = self.rows()
        item = {"source": "lancamento", "ref_id": a, "ym": "2026-10"}
        bank.reconcile(self.conn, r1["id"], item, TODAY)
        with self.assertRaises(ValueError):
            bank.reconcile(self.conn, r2["id"], item, TODAY)
        with self.assertRaises(ValueError):
            bank.reconcile(self.conn, r1["id"], item, TODAY)  # já tratada
        self.imp(make_ofx([("20261010", "100.00", "3", "Z")]))
        z = next(r for r in self.rows() if r["memo"] == "Z")
        e = self.tx("2026-10-10", "saida", 100, "outra")
        with self.assertRaises(ValueError):
            bank.reconcile(self.conn, z["id"], {"source": "lancamento", "ref_id": e, "ym": "2026-10"}, TODAY)

    def test_ignorar_restaurar_e_reconciliar_tudo(self):
        self.tx("2026-10-10", "saida", 100, "A")
        self.tx("2026-10-11", "saida", 50, "B")
        self.imp(make_ofx([("20261010", "-100.00", "1", "X"), ("20261011", "-50.00", "2", "Y"), ("20261012", "-7.00", "3", "Z")]))
        res = bank.reconcile_all(self.conn, TODAY)
        self.assertEqual(res, {"reconciled": 2, "failed": 0, "skipped": 0})
        z = self.rows()[0]
        bank.set_ignored(self.conn, z["id"], True)
        self.assertEqual(self.rows(), [])
        self.assertEqual(len(self.rows("ignorada")), 1)
        bank.set_ignored(self.conn, z["id"], False)
        self.assertEqual(bank.overview(self.conn, "pendente", TODAY)["counts"], {"pendente": 1, "conciliada": 2, "criada": 0, "ignorada": 0})

    def test_fatura_paga_no_banco_concilia_com_a_fatura(self):
        cid = cards.save_card(self.conn, {"name": "Nubank", "closing_day": 10, "due_day": 17})
        self.tx("2026-10-03", "saida", 200, "Mercado", card_id=cid)
        self.tx("2026-10-05", "saida", 90.5, "Farmácia", card_id=cid)
        self.imp(make_ofx([("20261016", "-290.50", "P1", "PAGTO FATURA NUBANK")]))
        row = self.rows()[0]
        self.assertEqual(row["suggestion"]["source"], "fatura")
        bank.reconcile(self.conn, row["id"], row["suggestion"], TODAY)
        self.assertTrue(next(i for i in finance.month_view(self.conn, "2026-10", TODAY)["items"] if i["source"] == "fatura")["reconciled"])

    def test_extrato_do_cartao_concilia_com_compras(self):
        cid = cards.save_card(self.conn, {"name": "Nubank", "closing_day": 10, "due_day": 17})
        t1 = self.tx("2026-10-03", "saida", 89.9, "iFood", card_id=cid)
        self.imp(make_ofx([("20261004", "-89.90", "K1", "IFOOD"), ("20261008", "-15.00", "K2", "UBER")], kind="cartao"), card_id=cid)
        rows = {r["memo"]: r for r in self.rows()}
        self.assertEqual(rows["IFOOD"]["suggestion"]["ref_id"], t1)
        bank.reconcile(self.conn, rows["IFOOD"]["id"], rows["IFOOD"]["suggestion"], TODAY)
        bank.create_from(self.conn, rows["UBER"]["id"], {}, TODAY)  # vira compra no cartão
        self.assertEqual(self.conn.execute("SELECT card_id FROM transactions WHERE amount = 15").fetchone()[0], cid)

    def test_diferenca_de_saldo_do_extrato(self):
        self.conn.execute("UPDATE accounts SET opening_balance = 1000")
        self.tx("2026-10-05", "entrada", 500, "Freela")
        self.imp(make_ofx([("20261005", "500.00", "1", "TED")], ledger=("20261018", "1450.00")))
        chk = bank.overview(self.conn, "pendente", TODAY)["balance_check"]
        self.assertEqual((chk["bank"], chk["app"], chk["diff"]), (1450.0, 1500.0, -50.0))

    def test_excluir_importacao_desfaz_marcas(self):
        self.imp(make_ofx([("20261018", "-1800.00", "A1", "PIX ALUGUEL")]))
        row = self.rows()[0]
        bank.reconcile(self.conn, row["id"], row["suggestion"], TODAY)
        imp_id = bank.overview(self.conn, "todas", TODAY)["imports"][0]["id"]
        bank.delete_import(self.conn, imp_id)
        self.assertEqual(self.rows("todas"), [])
        self.assertEqual(finance.month_view(self.conn, "2026-10", TODAY)["summary"]["a_pagar"], 1800)
        with self.assertRaises(ValueError):
            bank.delete_import(self.conn, imp_id)


    # ------------------------------------------------------------ suspeita de duplicidade

    def row(self, memo):
        return next(r for r in self.rows("todas") if r["memo"] == memo)

    def count_tx(self):
        return self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

    def test_valor_igual_fora_da_janela_e_suspeito_e_nao_cria(self):
        self.tx("2026-10-01", "saida", 120, "Supermercado")  # 12 dias antes: fora da janela de sugestão
        self.imp(make_ofx([("20261013", "-120.00", "1", "COMPRA MERCADO")]))
        r = self.row("COMPRA MERCADO")
        self.assertIsNone(r["suggestion"])
        self.assertEqual(len(r["suspects"]), 1)
        self.assertEqual(r["suspects"][0]["description"], "Supermercado")
        out = bank.create_from(self.conn, r["id"], {}, TODAY)
        self.assertTrue(out["duplicate_suspected"])
        self.assertEqual(self.count_tx(), 1)  # nada foi criado
        self.assertEqual(self.row("COMPRA MERCADO")["status"], "pendente")
        bank.create_from(self.conn, r["id"], {"force": True}, TODAY)  # decisão explícita do usuário
        self.assertEqual(self.count_tx(), 2)

    def test_nome_parecido_e_valor_proximo_e_suspeito(self):
        self.tx("2026-10-10", "saida", 99.90, "Netflix assinatura")
        self.imp(make_ofx([("20261012", "-100.40", "1", "NETFLIX COM")]))
        r = self.row("NETFLIX COM")
        self.assertIsNone(r["suggestion"])
        self.assertTrue(r["suspects"])
        self.assertEqual(bank.create_all(self.conn, TODAY), {"created": 0, "skipped": 1})
        self.assertEqual(self.count_tx(), 1)

    def test_lancar_todas_pula_suspeitas_e_cria_o_resto(self):
        self.tx("2026-10-01", "saida", 120, "Supermercado")
        self.imp(make_ofx([("20261013", "-120.00", "1", "COMPRA MERCADO"), ("20261014", "-33.33", "2", "LOJA NOVA")]))
        self.assertEqual(bank.create_all(self.conn, TODAY), {"created": 1, "skipped": 1})
        self.assertEqual(self.count_tx(), 2)
        self.assertEqual(self.row("COMPRA MERCADO")["status"], "pendente")
        self.assertEqual(self.row("LOJA NOVA")["status"], "criada")
        ov = bank.overview(self.conn, "pendente", TODAY)
        self.assertEqual((ov["to_review"], ov["unmatched"], ov["suggested"]), (1, 0, 0))

    def test_linhas_iguais_no_mesmo_extrato_nao_sao_suspeitas_entre_si(self):
        self.imp(make_ofx([("20261010", "-10.00", "1", "CAFE"), ("20261010", "-10.00", "2", "CAFE")]))
        for r in self.rows():
            self.assertEqual(r["suspects"], [])
        self.assertEqual(bank.create_all(self.conn, TODAY), {"created": 2, "skipped": 0})

    def test_mesma_linha_em_outro_extrato_e_suspeita(self):
        self.imp(make_ofx([("20261010", "-77.00", "A", "PADARIA SOL")]))
        bank.create_all(self.conn, TODAY)
        self.imp(make_ofx([("20261010", "-77.00", "B", "PADARIA SOL")], acct="99999-9"))  # outro arquivo, outro FITID
        r = next(x for x in self.rows() if x["memo"] == "PADARIA SOL")
        self.assertTrue(r["suspects"])
        self.assertEqual(bank.create_all(self.conn, TODAY), {"created": 0, "skipped": 1})

    def test_sugestao_ambigua_nao_entra_na_conciliacao_em_lote(self):
        self.tx("2026-10-10", "saida", 100, "Farmácia")
        self.tx("2026-10-11", "saida", 100, "Mercado")  # dois lançamentos iguais perto da data
        self.imp(make_ofx([("20261010", "-100.00", "1", "COMPRA")]))
        r = self.rows()[0]
        self.assertEqual(r["suggestion"]["ambiguous"], 1)
        self.assertEqual(bank.reconcile_all(self.conn, TODAY), {"reconciled": 0, "failed": 0, "skipped": 1})
        self.assertEqual(self.rows()[0]["status"], "pendente")
        bank.reconcile(self.conn, r["id"], r["suggestion"], TODAY)  # mas o usuário pode confirmar manualmente
        self.assertEqual(self.rows("conciliada")[0]["status"], "conciliada")

    def test_duas_linhas_disputando_um_lancamento_ficam_para_revisao(self):
        self.tx("2026-10-10", "saida", 100, "Farmácia")
        self.imp(make_ofx([("20261009", "-100.00", "1", "COMPRA 1"), ("20261011", "-100.00", "2", "COMPRA 2")]))
        self.assertEqual(bank.reconcile_all(self.conn, TODAY)["reconciled"], 0)
        self.assertEqual(bank.create_all(self.conn, TODAY), {"created": 0, "skipped": 1})  # a que perdeu a disputa é suspeita, não vira novo
        self.assertEqual(self.count_tx(), 1)

    def test_pagamento_de_fatura_parece_duplicata(self):
        cards.save_card(self.conn, {"name": "Visa", "closing_day": 5, "due_day": 12})
        self.imp(make_ofx([("20261012", "-850.00", "1", "PAGTO FATURA CARTAO VISA")]))
        r = self.rows()[0]
        self.assertTrue(r["suspects"])
        self.assertEqual(bank.create_all(self.conn, TODAY), {"created": 0, "skipped": 1})

    def test_lancamento_sem_suspeita_cria_normal(self):
        self.imp(make_ofx([("20261010", "-12.00", "1", "BANCA JORNAL")]))
        r = self.rows()[0]
        self.assertEqual(r["suspects"], [])
        self.assertIn("transaction_id", bank.create_from(self.conn, r["id"], {}, TODAY))


    # ------------------------------------------------------------ conciliação em grupo (1 pagamento = N dívidas, N pagamentos = 1 dívida)

    def pending_item(self, desc, amount, date="2026-10-25"):
        return {"source": "lancamento", "ref_id": self.tx(date, "saida", amount, desc), "ym": date[:7]}

    def a_pagar(self):
        return finance.month_view(self.conn, "2026-10", TODAY)["summary"]["a_pagar"]

    def test_um_pagamento_para_duas_dividas(self):
        a, b = self.pending_item("Carlos parcela 1", 200), self.pending_item("Carlos parcela 2", 300, "2026-10-28")
        base = self.a_pagar()
        self.imp(make_ofx([("20261018", "-500.00", "1", "PIX CARLOS")]))
        r = self.rows()[0]
        res = bank.reconcile_group(self.conn, {"txn_ids": [r["id"]], "items": [a, b]}, TODAY)
        self.assertEqual((res["txns"], res["items"]), (1, 2))
        self.assertEqual(self.a_pagar(), base - 500)  # as duas foram marcadas como pagas
        done = self.rows("conciliada")[0]
        self.assertEqual(sorted(i["description"] for i in done["matched_items"]), ["Carlos parcela 1", "Carlos parcela 2"])
        self.assertEqual(done["group_size"], 1)
        items = finance.month_view(self.conn, "2026-10", TODAY)["items"]
        self.assertTrue(all(i["reconciled"] for i in items if i["description"].startswith("Carlos")))
        bank.unreconcile(self.conn, done["id"], TODAY)
        self.assertEqual(self.a_pagar(), base)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM bank_links").fetchone()[0], 0)

    def test_soma_precisa_bater_e_nada_muda_se_nao_bater(self):
        a, b = self.pending_item("Dívida 1", 200), self.pending_item("Dívida 2", 300)
        self.imp(make_ofx([("20261018", "-499.00", "1", "PIX")]))
        r = self.rows()[0]
        with self.assertRaises(ValueError) as ctx:
            bank.reconcile_group(self.conn, {"txn_ids": [r["id"]], "items": [a, b]}, TODAY)
        self.assertIn("soma não bate", str(ctx.exception))
        self.assertEqual(self.rows()[0]["status"], "pendente")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM payment_marks").fetchone()[0], 0)

    def test_dois_pagamentos_para_uma_divida(self):
        a = self.pending_item("Fornecedor X", 500)
        base = self.a_pagar()
        self.imp(make_ofx([("20261015", "-200.00", "1", "PIX FORNECEDOR X"), ("20261018", "-300.00", "2", "PIX FORNECEDOR X")]))
        ids = [r["id"] for r in self.rows()]
        bank.reconcile_group(self.conn, {"txn_ids": ids, "items": [a]}, TODAY)
        self.assertEqual(self.a_pagar(), base - 500)
        done = self.rows("conciliada")
        self.assertEqual([r["group_size"] for r in done], [2, 2])
        bank.unreconcile(self.conn, ids[0], TODAY)  # desfazer uma linha desfaz o grupo todo
        self.assertEqual(self.a_pagar(), base)
        self.assertEqual({r["status"] for r in self.rows("todas")}, {"pendente"})

    def test_grupo_rejeita_mistura_item_usado_e_repeticao(self):
        a = self.pending_item("A", 100)
        b = self.pending_item("B", 100)
        e = {"source": "lancamento", "ref_id": self.tx("2026-10-25", "entrada", 100, "Entrada"), "ym": "2026-10"}
        self.imp(make_ofx([("20261018", "-100.00", "1", "X"), ("20261018", "100.00", "2", "Y"), ("20261018", "-100.00", "3", "Z")]))
        by = {r["memo"]: r["id"] for r in self.rows()}
        with self.assertRaises(ValueError):  # saída e entrada juntas
            bank.reconcile_group(self.conn, {"txn_ids": [by["X"], by["Y"]], "items": [a]}, TODAY)
        with self.assertRaises(ValueError):  # entrada do app para saída do banco
            bank.reconcile_group(self.conn, {"txn_ids": [by["X"]], "items": [e]}, TODAY)
        with self.assertRaises(ValueError):  # repetido
            bank.reconcile_group(self.conn, {"txn_ids": [by["X"]], "items": [a, a]}, TODAY)
        with self.assertRaises(ValueError):
            bank.reconcile_group(self.conn, {"txn_ids": [by["X"], by["X"]], "items": [a]}, TODAY)
        bank.reconcile_group(self.conn, {"txn_ids": [by["X"]], "items": [a]}, TODAY)
        with self.assertRaises(ValueError):  # a já foi usado por X
            bank.reconcile_group(self.conn, {"txn_ids": [by["Z"]], "items": [a]}, TODAY)
        bank.reconcile_group(self.conn, {"txn_ids": [by["Z"]], "items": [b]}, TODAY)

    def test_candidatos_do_grupo(self):
        self.pending_item("Dívida 1", 200)
        self.tx("2026-04-01", "saida", 50, "Antiga demais")
        self.imp(make_ofx([("20261018", "-500.00", "1", "PIX")]))
        c = bank.group_candidates(self.conn, [self.rows()[0]["id"]], TODAY)
        self.assertEqual(c["total"], 500.0)
        names = [i["description"] for i in c["items"]]
        self.assertIn("Dívida 1", names)
        self.assertNotIn("Antiga demais", names)

    def test_dica_de_soma_para_dividas_da_mesma_pessoa_e_nao_lanca_em_lote(self):
        self.pending_item("Carlos Silva honorários", 200)
        self.pending_item("Carlos Silva multa", 300, "2026-10-28")
        self.imp(make_ofx([("20261018", "-500.00", "1", "PIX CARLOS SILVA")]))
        r = self.rows()[0]
        self.assertIsNone(r["suggestion"])
        self.assertEqual(len(r["group_hints"]), 1)
        self.assertEqual(r["group_hints"][0]["type"], "um_para_varios")
        self.assertTrue(r["suspects"])  # parece duplicar a soma: nunca é lançada em lote
        self.assertEqual(bank.create_all(self.conn, TODAY), {"created": 0, "skipped": 1})
        out = bank.create_from(self.conn, r["id"], {}, TODAY)
        self.assertTrue(out["duplicate_suspected"])
        h = r["group_hints"][0]
        bank.reconcile_group(self.conn, {"txn_ids": h["txn_ids"], "items": h["items"]}, TODAY)  # dica aplicada com um clique
        self.assertEqual(self.rows("conciliada")[0]["status"], "conciliada")

    def test_sem_dica_quando_nomes_nao_tem_relacao(self):
        self.pending_item("Mercado", 200)
        self.pending_item("Oficina", 300, "2026-10-28")
        self.imp(make_ofx([("20261018", "-500.00", "1", "PIX FULANO")]))
        r = self.rows()[0]
        self.assertEqual(r["group_hints"], [])
        self.assertEqual(r["suspects"], [])  # soma coincide, mas nada indica relação: segue como linha nova

    def test_dica_de_duas_linhas_para_um_lancamento(self):
        self.pending_item("Fornecedor Alfa", 500)
        self.imp(make_ofx([("20261015", "-200.00", "1", "PIX FORNECEDOR ALFA"), ("20261018", "-300.00", "2", "PIX FORNECEDOR ALFA")]))
        rows = self.rows()
        self.assertTrue(all(r["group_hints"] and r["group_hints"][0]["type"] == "varios_para_um" for r in rows))
        self.assertEqual(bank.create_all(self.conn, TODAY), {"created": 0, "skipped": 2})

    def test_excluir_importacao_desfaz_grupo_e_mantem_outras_linhas(self):
        a = self.pending_item("Fornecedor X", 500)
        base = self.a_pagar()
        self.imp(make_ofx([("20261015", "-200.00", "1", "X1")], acct="A"))
        self.imp(make_ofx([("20261018", "-300.00", "2", "X2")], acct="B"))
        ids = [r["id"] for r in self.rows()]
        bank.reconcile_group(self.conn, {"txn_ids": ids, "items": [a]}, TODAY)
        first = bank.overview(self.conn, "todas", TODAY)["imports"][-1]["id"]
        bank.delete_import(self.conn, first)
        self.assertEqual(self.a_pagar(), base)
        self.assertEqual([r["status"] for r in self.rows("todas")], ["pendente"])  # a outra linha voltou a pendente

    def test_migracao_do_vinculo_antigo(self):
        a = self.tx("2026-10-10", "saida", 40, "Antigo")
        self.imp(make_ofx([("20261010", "-40.00", "1", "ANTIGO")]))
        tid = self.rows()[0]["id"]
        self.conn.execute("ALTER TABLE bank_txns ADD COLUMN matched_source TEXT")
        self.conn.execute("ALTER TABLE bank_txns ADD COLUMN matched_ref_id INTEGER")
        self.conn.execute("ALTER TABLE bank_txns ADD COLUMN matched_ym TEXT")
        self.conn.execute("ALTER TABLE bank_txns ADD COLUMN marked INTEGER NOT NULL DEFAULT 0")
        self.conn.execute("UPDATE bank_txns SET status = 'conciliada', matched_source = 'lancamento', matched_ref_id = ?, "
                          "matched_ym = '2026-10' WHERE id = ?", (a, tid))
        db.init(self.conn)
        self.assertEqual([(l["source"], l["ref_id"], l["ym"]) for l in self.conn.execute("SELECT * FROM bank_links")],
                         [("lancamento", a, "2026-10")])
        self.assertEqual(self.rows("conciliada")[0]["matched_description"], "Antigo")
        db.init(self.conn)  # idempotente
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM bank_links").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
