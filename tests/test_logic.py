"""Testes da lógica de cálculo.  Rodar com:  python -m unittest discover -s tests -v"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site" / "py"))

from financas import db, finance, invest, reports, validate  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.init(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def tx(self, date, kind, amount, desc="x", cat=""):
        return finance.save_transaction(self.conn, {"date": date, "kind": kind, "amount": amount,
                                                     "description": desc, "category": cat})

    def month(self, ym):
        return finance.month_view(self.conn, ym, "2026-12-31")["summary"]

    def buy(self, ticker, date, qty, price, fees=0, cash=True, kind="acao"):
        return invest.save_op(self.conn, {"ticker": ticker, "op": "compra", "date": date, "quantity": qty,
                                          "price": price, "fees": fees, "affects_cash": cash, "kind": kind})

    def sell(self, ticker, date, qty, price):
        return invest.save_op(self.conn, {"ticker": ticker, "op": "venda", "date": date, "quantity": qty,
                                          "price": price})

    def pos(self, ticker, today="2026-06-15"):
        return next(p for p in invest.positions(self.conn, today) if p["ticker"] == ticker)


class TestParse(unittest.TestCase):
    def test_parse_br(self):
        cases = {"1.234,56": 1234.56, "1234,56": 1234.56, "1234.56": 1234.56, "R$ 12,5": 12.5,
                 "1.234": 1234.0, "12.5": 12.5, "0.123": 0.123, "1.234.567": 1234567.0, "30": 30.0}
        for raw, expected in cases.items():
            self.assertAlmostEqual(validate.parse_br(raw), expected, msg=raw)

    def test_number_errors(self):
        with self.assertRaises(ValueError):
            validate.number({"a": "abc"}, "a", "O valor")
        with self.assertRaises(ValueError):
            validate.number({"a": 0}, "a", "O valor")
        with self.assertRaises(ValueError):
            validate.number({"a": -5}, "a", "O valor")


class TestCarryOver(Base):
    def test_balance_is_carried_to_next_month(self):
        self.tx("2026-01-05", "entrada", 1000)
        self.tx("2026-01-10", "saida", 300)
        self.tx("2026-02-03", "saida", 50)
        jan, feb = self.month("2026-01"), self.month("2026-02")
        self.assertEqual((jan["entradas"], jan["saidas"], jan["saldo_final"]), (1000, 300, 700))
        self.assertEqual(feb["saldo_anterior"], 700)
        self.assertEqual(feb["saldo_final"], 650)

    def test_negative_balance_is_carried_too(self):
        self.tx("2026-01-05", "entrada", 100)
        self.tx("2026-02-05", "saida", 400)
        self.assertEqual(self.month("2026-02")["saldo_final"], -300)
        self.assertEqual(self.month("2026-03")["saldo_anterior"], -300)

    def test_opening_balance_and_month(self):
        reports.save_settings(self.conn, {"cdi_annual": 13.65, "opening_balance": 500, "opening_month": "2026-01"})
        self.tx("2025-12-10", "saida", 999)  # antes do início do controle: fora do cálculo
        self.tx("2026-01-10", "entrada", 100)
        self.assertEqual(self.month("2026-01")["saldo_anterior"], 500)
        self.assertEqual(self.month("2026-01")["saldo_final"], 600)
        self.assertTrue(finance.month_view(self.conn, "2025-12", "2026-12-31")["outside_control"])

    def test_fixed_items_respect_start_end_and_short_months(self):
        finance.save_fixed(self.conn, {"kind": "saida", "description": "Aluguel", "amount": 100, "day": 31,
                                       "start_month": "2026-01", "end_month": "2026-03"})
        feb = finance.month_view(self.conn, "2026-02", "2026-12-31")
        self.assertEqual(feb["items"][0]["date"], "2026-02-28")
        self.assertEqual(self.month("2026-03")["saidas"], 100)
        self.assertEqual(finance.month_view(self.conn, "2026-04", "2026-12-31")["summary"]["saidas"], 0)
        # saldo acumulado: -100 por mês enquanto durar
        self.assertEqual(self.month("2026-03")["saldo_final"], -300)

    def test_fixed_income_item(self):
        finance.save_fixed(self.conn, {"kind": "entrada", "description": "Salário", "amount": 5000, "day": 5,
                                       "start_month": "2026-01"})
        self.assertEqual(self.month("2026-08")["entradas"], 5000)

    def test_installments(self):
        finance.save_installment(self.conn, {"description": "Notebook", "installment_amount": 200,
                                             "first_date": "2026-02-10", "n_installments": 3})
        tags = [finance.month_view(self.conn, f"2026-{m:02d}", "2026-12-31")["items"] for m in (2, 3, 4, 5)]
        self.assertEqual([i[0]["tag"] if i else None for i in tags], ["1/3", "2/3", "3/3", None])
        listed = finance.list_installments(self.conn, "2026-03")[0]
        self.assertEqual((listed["paid"], listed["remaining"], listed["remaining_amount"], listed["total"]),
                         (2, 1, 200, 600))
        self.assertEqual(listed["last_month"], "2026-04")
        self.assertEqual(finance.list_installments(self.conn, "2026-01")[0]["status"], "futura")
        self.assertEqual(finance.list_installments(self.conn, "2026-05")[0]["status"], "quitada")

    def test_category_breakdown(self):
        self.tx("2026-01-05", "saida", 100, cat="Mercado")
        self.tx("2026-01-06", "saida", 50, cat="Mercado")
        self.tx("2026-01-07", "saida", 70, cat="Lazer")
        cats = finance.month_view(self.conn, "2026-01", "2026-12-31")["by_category"]
        self.assertEqual(cats[0], {"category": "Mercado", "total": 150})

    def test_validation_messages(self):
        with self.assertRaises(ValueError):
            self.tx("2026-13-40", "saida", 10)
        with self.assertRaises(ValueError):
            finance.save_fixed(self.conn, {"kind": "saida", "description": "x", "amount": 1, "day": 5,
                                           "start_month": "2026-05", "end_month": "2026-01"})


class TestPendingIncome(Base):
    """Entradas com data posterior a hoje ficam em 'a receber' e não compõem o saldo."""
    TODAY = "2026-06-15"

    def view(self, ym, today=None):
        return finance.month_view(self.conn, ym, today or self.TODAY)

    def test_future_income_stays_out_of_balance(self):
        self.tx("2026-06-10", "entrada", 1000)
        self.tx("2026-06-20", "entrada", 500)
        self.tx("2026-06-12", "saida", 200)
        v = self.view("2026-06")
        s = v["summary"]
        self.assertEqual((s["entradas"], s["a_receber"], s["saldo_final"], s["saldo_previsto"]), (1000, 500, 800, 1300))
        flags = {(i["date"], i["kind"]): i["pending"] for i in v["items"]}
        self.assertEqual(flags, {("2026-06-10", "entrada"): False, ("2026-06-12", "saida"): False,
                                 ("2026-06-20", "entrada"): True})

    def test_today_itself_counts_as_received(self):
        self.tx("2026-06-15", "entrada", 300)
        s = self.view("2026-06")["summary"]
        self.assertEqual((s["entradas"], s["a_receber"], s["saldo_final"]), (300, 0, 300))

    def test_it_becomes_received_when_the_day_arrives(self):
        self.tx("2026-06-20", "entrada", 500)
        self.assertEqual(self.view("2026-06")["summary"]["saldo_final"], 0)
        s = self.view("2026-06", "2026-06-20")["summary"]
        self.assertEqual((s["saldo_final"], s["a_receber"]), (500, 0))

    def test_future_fixed_income_and_carry_over(self):
        self.tx("2026-06-12", "saida", 200)
        finance.save_fixed(self.conn, {"kind": "entrada", "description": "Salário", "amount": 3000, "day": 5,
                                       "start_month": "2026-06"})
        finance.save_fixed(self.conn, {"kind": "entrada", "description": "Aluguel recebido", "amount": 800,
                                       "day": 25, "start_month": "2026-06"})
        jun, jul = self.view("2026-06")["summary"], self.view("2026-07")["summary"]
        # junho: salário (dia 5) já caiu; aluguel (dia 25) ainda não
        self.assertEqual((jun["entradas"], jun["a_receber"], jun["saldo_final"]), (3000, 800, 2800))
        # julho: tudo futuro; o saldo transportado não inclui nada a receber
        self.assertEqual((jul["saldo_anterior"], jul["entradas"], jul["a_receber"], jul["saldo_final"]),
                         (2800, 0, 3800, 2800))
        # previsão: considera tudo que está cadastrado
        self.assertEqual((jun["saldo_previsto"], jul["saldo_previsto"]), (3600, 7400))

    def test_future_dividend_is_pending(self):
        self.buy("HGLG11", "2026-01-10", 10, 100, cash=False, kind="fii")
        invest.save_dividend(self.conn, {"ticker": "HGLG11", "date": "2026-06-25", "amount": 50})
        s = self.view("2026-06")["summary"]
        self.assertEqual((s["entradas"], s["a_receber"]), (0, 50))

    def test_investment_movements_are_not_affected(self):
        self.tx("2026-06-02", "entrada", 5000)
        self.buy("PETR4", "2026-06-25", 10, 10)  # compra agendada: continua saindo do caixa do mês
        self.assertEqual(self.view("2026-06")["summary"]["saldo_final"], 4900)

    def test_overview_balance_ignores_pending(self):
        self.tx("2026-06-02", "entrada", 1000)
        self.tx("2026-06-28", "entrada", 400)
        o = reports.overview(self.conn, self.TODAY)
        self.assertEqual((o["conta"], o["patrimonio_total"], o["current_month"]["a_receber"]), (1000, 1000, 400))


class TestStocks(Base):
    def test_average_price_recalculated_on_each_buy(self):
        self.buy("PETR4", "2026-01-10", 100, 10)
        self.assertAlmostEqual(self.pos("PETR4")["avg_price"], 10)
        self.buy("PETR4", "2026-02-10", 100, 20)
        p = self.pos("PETR4")
        self.assertEqual((p["qty"], p["avg_price"], p["cost"]), (200, 15, 3000))

    def test_fees_are_part_of_average_price(self):
        self.buy("HGLG11", "2026-01-10", 10, 100, fees=10, kind="fii")
        self.assertAlmostEqual(self.pos("HGLG11")["avg_price"], 101)

    def test_sale_keeps_average_and_tracks_realized(self):
        self.buy("PETR4", "2026-01-10", 100, 10)
        self.buy("PETR4", "2026-02-10", 100, 20)
        self.sell("PETR4", "2026-03-10", 50, 30)
        p = self.pos("PETR4")
        self.assertEqual((p["qty"], p["avg_price"], p["realized"]), (150, 15, 750))

    def test_cannot_sell_more_than_owned(self):
        self.buy("PETR4", "2026-01-10", 10, 10)
        with self.assertRaises(ValueError):
            self.sell("PETR4", "2026-02-10", 11, 10)
        with self.assertRaises(ValueError):
            self.sell("VALE3", "2026-02-10", 1, 10)

    def test_cannot_delete_buy_needed_by_later_sale(self):
        buy_id = self.buy("PETR4", "2026-01-10", 10, 10)
        self.sell("PETR4", "2026-02-10", 10, 12)
        with self.assertRaises(ValueError):
            invest.delete_op(self.conn, buy_id)

    def test_unknown_asset_needs_kind(self):
        with self.assertRaises(ValueError):
            invest.save_op(self.conn, {"ticker": "ABCD3", "op": "compra", "date": "2026-01-01", "quantity": 1,
                                       "price": 1})

    def test_yield_on_cost(self):
        self.buy("HGLG11", "2026-01-10", 100, 30, kind="fii")  # custo 3000
        for month in ("2026-02-15", "2026-03-15", "2026-04-15"):
            invest.save_dividend(self.conn, {"ticker": "HGLG11", "date": month, "amount": 30})
        p = self.pos("HGLG11")
        self.assertEqual((p["div_12m"], p["yoc_12m"]), (90, 3.0))
        # aportar mais muda o custo e, portanto, o YoC
        self.buy("HGLG11", "2026-05-10", 100, 30, kind="fii")
        self.assertEqual(self.pos("HGLG11")["yoc_12m"], 1.5)

    def test_dividends_older_than_12_months_are_excluded(self):
        self.buy("HGLG11", "2024-01-10", 100, 30, kind="fii")
        invest.save_dividend(self.conn, {"ticker": "HGLG11", "date": "2025-01-15", "amount": 50})
        invest.save_dividend(self.conn, {"ticker": "HGLG11", "date": "2026-05-15", "amount": 40})
        p = self.pos("HGLG11")
        self.assertEqual((p["div_12m"], p["div_total"]), (40, 90))

    def test_market_value_uses_manual_quote(self):
        self.buy("PETR4", "2026-01-10", 100, 10)
        asset_id = self.pos("PETR4")["id"]
        invest.set_price(self.conn, asset_id, {"price": "12,50"})
        p = self.pos("PETR4")
        self.assertEqual((p["value"], p["result"], p["result_pct"]), (1250, 250, 25.0))

    def test_dividend_requires_existing_asset(self):
        with self.assertRaises(ValueError):
            invest.save_dividend(self.conn, {"ticker": "XXXX3", "date": "2026-01-01", "amount": 1})

    def test_asset_removed_when_last_record_deleted(self):
        op = self.buy("PETR4", "2026-01-10", 10, 10)
        invest.delete_op(self.conn, op)
        self.assertEqual(invest.positions(self.conn, "2026-06-15"), [])


class TestCashLink(Base):
    def setUp(self):
        super().setUp()
        self.tx("2026-01-02", "entrada", 5000)

    def test_purchase_reduces_account_balance_when_flagged(self):
        self.buy("PETR4", "2026-01-10", 100, 10, fees=5)
        jan = self.month("2026-01")
        self.assertEqual((jan["aportes"], jan["saldo_final"]), (1005, 3995))

    def test_purchase_can_be_kept_out_of_the_balance(self):
        self.buy("PETR4", "2026-01-10", 100, 10, cash=False)
        self.assertEqual(self.month("2026-01")["saldo_final"], 5000)

    def test_sale_and_dividend_return_to_account(self):
        self.buy("PETR4", "2026-01-10", 100, 10, cash=False)
        self.sell("PETR4", "2026-01-20", 10, 12)
        invest.save_dividend(self.conn, {"ticker": "PETR4", "date": "2026-01-25", "amount": 30})
        jan = self.month("2026-01")
        # compra ficou de fora do caixa; a venda (10 x 12) e o provento (30) entram por padrão
        self.assertEqual(jan["aportes"], -120)
        self.assertEqual(jan["entradas"], 5030)
        self.assertEqual(jan["saldo_final"], 5150)

    def test_fixed_income_cash_flow(self):
        fi = invest.save_fi(self.conn, {"name": "CDB", "pct_cdi": 100})
        invest.save_fi_move(self.conn, {"fi_id": fi, "kind": "aporte", "date": "2026-01-10", "amount": 1000})
        invest.save_fi_move(self.conn, {"fi_id": fi, "kind": "rendimento", "date": "2026-01-31", "amount": 9})
        jan = self.month("2026-01")
        self.assertEqual((jan["aportes"], jan["saldo_final"]), (1000, 4000))  # rendimento não mexe no caixa


class TestFixedIncome(Base):
    def test_monthly_rate(self):
        self.assertAlmostEqual(invest.monthly_rate(12, 100), 1.12 ** (1 / 12) - 1)
        self.assertAlmostEqual(invest.monthly_rate(12, 110), 1.132 ** (1 / 12) - 1)

    def test_balance_and_validation(self):
        fi = invest.save_fi(self.conn, {"name": "CDB Banco X", "pct_cdi": 105})
        invest.save_fi_move(self.conn, {"fi_id": fi, "kind": "aporte", "date": "2026-01-10", "amount": 1000})
        invest.save_fi_move(self.conn, {"fi_id": fi, "kind": "rendimento", "date": "2026-02-10", "amount": 10})
        with self.assertRaises(ValueError):
            invest.save_fi_move(self.conn, {"fi_id": fi, "kind": "resgate", "date": "2026-03-01", "amount": 2000})
        invest.save_fi_move(self.conn, {"fi_id": fi, "kind": "resgate", "date": "2026-03-01", "amount": 500})
        p = invest.fi_products(self.conn, "2026-06-15", 13.65)[0]
        self.assertEqual((p["saldo"], p["aportado"], p["rendimento_total"]), (510, 500, 10))
        self.assertAlmostEqual(p["taxa_anual"], 14.33, places=2)

    def test_projection_year(self):
        fi = invest.save_fi(self.conn, {"name": "CDB", "pct_cdi": 100})
        invest.save_fi_move(self.conn, {"fi_id": fi, "kind": "aporte", "date": "2026-01-10", "amount": 10000})
        invest.save_fi_move(self.conn, {"fi_id": fi, "kind": "rendimento", "date": "2026-05-31", "amount": 100})
        proj = invest.projection(self.conn, "2026-06-15", 12.0)
        rows = proj["rows"]
        self.assertEqual(len(rows), 12)
        self.assertFalse(rows[5]["projected"])
        self.assertTrue(rows[6]["projected"])
        m = 1.12 ** (1 / 12) - 1
        self.assertAlmostEqual(rows[6]["rf_yield"], 10100 * m, places=1)  # julho rende sobre saldo de junho
        self.assertAlmostEqual(rows[11]["rf"], 10100 * (1 + m) ** 6, places=1)
        self.assertAlmostEqual(proj["summary"]["proj_rf"], 10100 * ((1 + m) ** 6 - 1), places=1)
        self.assertEqual(proj["summary"]["ytd_rf"], 100)

    def test_projection_of_dividends_uses_monthly_average(self):
        self.buy("HGLG11", "2026-01-10", 100, 30, kind="fii")
        for m in ("2026-03-15", "2026-04-15", "2026-05-15"):
            invest.save_dividend(self.conn, {"ticker": "HGLG11", "date": m, "amount": 30})
        proj = invest.projection(self.conn, "2026-06-15", 13.65)
        # 90 recebidos em 4 meses (mar..jun) -> média 22,50/mês; restam 6 meses
        self.assertEqual(proj["avg_monthly_dividends"], 22.5)
        self.assertEqual(proj["summary"]["ytd_div"], 90)
        self.assertEqual(proj["summary"]["proj_div"], 135)
        self.assertAlmostEqual(proj["summary"]["yoc_year"], 225 / 3000 * 100, places=2)


class TestReports(Base):
    def test_overview_adds_account_and_investments(self):
        self.tx("2026-06-02", "entrada", 5000)
        fi = invest.save_fi(self.conn, {"name": "CDB", "pct_cdi": 100})
        invest.save_fi_move(self.conn, {"fi_id": fi, "kind": "aporte", "date": "2026-06-03", "amount": 1000})
        self.buy("PETR4", "2026-06-04", 100, 10)
        o = reports.overview(self.conn, "2026-06-15")
        self.assertEqual(o["conta"], 3000)  # 5000 - 1000 - 1000
        self.assertEqual((o["renda_fixa"], o["renda_variavel"], o["investido"]), (1000, 1000, 2000))
        self.assertEqual(o["patrimonio_total"], 5000)
        self.assertEqual(len(o["evolution"]), 12)
        labels = {a["label"] for a in o["allocation"]}
        self.assertEqual(labels, {"Conta", "Ações", "Renda fixa (CDI)"})

    def test_empty_database_does_not_break(self):
        o = reports.overview(self.conn, "2026-06-15")
        self.assertEqual(o["patrimonio_total"], 0)
        d = reports.invest_dashboard(self.conn, "2026-06-15")
        self.assertEqual(d["totals"]["total"], 0)
        self.assertEqual(len(d["projection"]["rows"]), 12)

    def test_settings_roundtrip(self):
        s = reports.save_settings(self.conn, {"cdi_annual": "14,25", "opening_balance": "-120,50",
                                              "opening_month": ""})
        self.assertEqual(s, {"cdi_annual": 14.25, "opening_balance": -120.5, "opening_month": ""})


if __name__ == "__main__":
    unittest.main()
