"""Teste com dados variados (duas contas, cartão, parcelas, fixas, ações, renda fixa, extrato OFX).
Sobe o próprio servidor de teste, cria os dados pela API e confere todas as telas no celular.

Uso:  python tests/e2e_dados.py [pasta_das_capturas]
"""
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_bank import make_ofx  # noqa: E402

PORT = 8793
BASE = f"http://127.0.0.1:{PORT}"
SHOTS = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/shots-dados")
SHOTS.mkdir(parents=True, exist_ok=True)
DATA = Path("/tmp/mc-dados-dados")
TODAY = "2026-10-21"
failures = []


def check(cond, msg):
    print(("  ok   " if cond else "  FALHA ") + msg)
    if not cond:
        failures.append(msg)


def call(method, path, body=None):
    req = urllib.request.Request(BASE + "/__api", data=json.dumps({"method": method, "path": path, "body": None if body is None else json.dumps(body), "today": TODAY}).encode(),
                                 headers={"Content-Type": "application/json"})
    res = json.loads(urllib.request.urlopen(req).read())
    assert res["status"] == 200, (method, path, res)
    return res["body"]


def eventually(page, selector, text, timeout=6000):
    try:
        page.wait_for_function("([s, t]) => { const e = document.querySelector(s); return !!e && e.innerText.includes(t); }", arg=[selector, text], timeout=timeout)
        return True
    except Exception:
        return False


shutil.rmtree(DATA, ignore_errors=True)
srv = subprocess.Popen([sys.executable, str(ROOT / "tests" / "dev_server.py"), "--port", str(PORT), "--data", str(DATA)], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
try:
    for _ in range(50):
        try:
            urllib.request.urlopen(BASE + "/", timeout=1)
            break
        except Exception:
            time.sleep(0.1)

    print("criando os dados")
    call("POST", "/api/wizard", {"account_name": "Itaú", "account_kind": "corrente", "opening_balance": 2500, "opening_month": "2026-07", "cdi_annual": 13.65})
    poup = call("POST", "/api/accounts", {"name": "Poupança", "kind": "poupanca", "opening_balance": 8000})["id"]
    call("POST", "/api/transfers", {"date": "2026-10-03", "from_account": 1, "to_account": poup, "amount": 300, "description": "Reserva"})
    card = call("POST", "/api/cards", {"name": "Nubank", "closing_day": 10, "due_day": 17})["id"]
    for ym, extra in (("2026-08", 0), ("2026-09", 0), ("2026-10", 0)):
        call("POST", "/api/transactions", {"date": f"{ym}-05", "kind": "entrada", "description": "Salário", "category": "Salário", "amount": 6500 + extra})
        call("POST", "/api/transactions", {"date": f"{ym}-08", "kind": "saida", "description": "Mercado", "category": "Mercado", "amount": 640.35})
        call("POST", "/api/transactions", {"date": f"{ym}-12", "kind": "saida", "description": "Uber", "category": "Transporte", "amount": 58.9, "card_id": card})
    call("POST", "/api/fixed", {"kind": "saida", "description": "Aluguel", "category": "Moradia", "amount": 1800, "day": 20, "start_month": "2026-07"})
    call("POST", "/api/fixed", {"kind": "saida", "description": "Internet", "category": "Assinaturas", "amount": 119.9, "day": 25, "start_month": "2026-07"})
    call("POST", "/api/installments", {"description": "Geladeira", "category": "Casa", "installment_amount": 389.9, "n_installments": 10, "first_date": "2026-08-15"})
    call("POST", "/api/budgets", {"category": "Mercado", "amount": 600})
    call("POST", "/api/ops", {"ticker": "PETR4", "kind": "acao", "op": "compra", "date": "2026-08-10", "quantity": 100, "price": 36.5, "fees": 4.9, "affects_cash": True})
    call("POST", "/api/ops", {"ticker": "MXRF11", "kind": "fii", "op": "compra", "date": "2026-08-11", "quantity": 300, "price": 10.1, "fees": 0, "affects_cash": True})
    call("POST", "/api/dividends", {"ticker": "MXRF11", "date": "2026-10-14", "amount": 33.0, "tipo": "Rendimento"})
    fi = call("POST", "/api/fi", {"name": "CDB Inter 110% CDI", "pct_cdi": 110, "tax": "regressivo"})["id"]
    call("POST", "/api/fi/moves", {"fi_id": fi, "date": "2026-09-01", "kind": "aporte", "amount": 5000, "affects_cash": True})

    ofx = make_ofx([("20261005", "6500.00", "F1", "SALARIO EMPRESA"), ("20261008", "-640.35", "F2", "MERCADO EXTRA"),
                    ("20261020", "-1800.00", "F3", "ALUGUEL IMOB"), ("20261015", "-89.90", "F4", "PIX FARMACIA")], ledger=("20261018", "9120.00"))
    ofx_path = SHOTS / "extrato-teste.ofx"
    ofx_path.write_bytes(ofx)

    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, has_touch=True, is_mobile=True, locale="pt-BR", timezone_id="America/Sao_Paulo")
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
        page.goto(BASE + "/#/visao")
        page.wait_for_selector(".hero")
        page.wait_for_timeout(600)
        hero = page.inner_text(".hero")
        check("Patrimônio total" in hero and "Renda fixa" in hero, "visão geral com patrimônio")
        page.screenshot(path=str(SHOTS / "10-visao.png"), full_page=True)
        for h in ("mes", "conc", "fixas", "invest", "relat", "config"):
            page.goto(f"{BASE}/#/{h}")
            page.wait_for_selector("#view > *:not(.loading):not(.boot)")
            page.wait_for_timeout(500)
            body = page.inner_text("#view")
            check("Algo deu errado" not in body, f"tela {h} abre com dados")
            page.screenshot(path=str(SHOTS / f"11-{h}.png"), full_page=True)
        page.goto(f"{BASE}/#/fixas")
        check(eventually(page, "#view", "Geladeira"), "parcelas aparecem em Fixas e parcelas")
        page.goto(f"{BASE}/#/invest")
        page.wait_for_selector("text=Ações e FIIs")
        page.click("text=Ações e FIIs")
        check(eventually(page, "#view", "PETR4"), "ações aparecem em Investimentos")

        print("importar extrato OFX pela tela")
        page.goto(f"{BASE}/#/conc")
        page.wait_for_selector("text=Importar extrato (OFX)")
        page.set_input_files("input[type=file][aria-label='Escolher extrato OFX']", str(ofx_path))
        page.wait_for_selector(".modal")
        check("4 de 4" in page.inner_text(".modal"), "pré-visualização do extrato: 4 linhas novas")
        page.screenshot(path=str(SHOTS / "12-importar.png"))
        page.click(".modal button.btn.primary")
        page.wait_for_selector(".modal", state="detached")
        page.wait_for_timeout(600)
        txt = page.inner_text("#view")
        check("SALARIO EMPRESA" in txt, "linhas do extrato aparecem para conciliar")
        page.screenshot(path=str(SHOTS / "13-conciliacao.png"), full_page=True)
        recon = page.query_selector("button:has-text('Conciliar as sugestões')")
        if recon:
            recon.click()
            page.wait_for_timeout(800)
            conf = page.query_selector(".modal button.btn.primary")
            if conf:
                conf.click()
                page.wait_for_timeout(600)
        check("Algo deu errado" not in page.inner_text("#view"), "conciliar sugestões sem erro")
        page.screenshot(path=str(SHOTS / "14-conciliacao-depois.png"), full_page=True)

        print("relatório com dados")
        page.goto(f"{BASE}/#/relat")
        page.wait_for_selector("text=Maiores gastos", timeout=8000)
        check(True, "relatório mostra maiores gastos")
        page.screenshot(path=str(SHOTS / "15-relatorio.png"), full_page=True)
        browser.close()
finally:
    srv.terminate()

real = [e for e in errors if "favicon" not in e.lower()]
for e in real:
    print("  erro no navegador:", e)
if real:
    failures.append("erros no console")
print("RESULTADO:", "FALHOU" if failures else "tudo certo")
sys.exit(1 if failures else 0)
