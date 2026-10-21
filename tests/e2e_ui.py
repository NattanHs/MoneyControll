"""Teste de ponta a ponta da interface (Chromium com tela de celular) contra tests/dev_server.py.

Uso:  python tests/e2e_ui.py http://127.0.0.1:8791 /tmp/shots
Não faz parte do `unittest discover` (precisa do Playwright e de um servidor de teste no ar).
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8791"
SHOTS = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/shots")
SHOTS.mkdir(parents=True, exist_ok=True)

errors = []
failures = []


def check(cond, msg):
    print(("  ok   " if cond else "  FALHA ") + msg)
    if not cond:
        failures.append(msg)


def eventually(page, selector, text, timeout=6000):
    """Espera o texto aparecer (a tela redesenha depois de cada chamada); devolve True/False."""
    try:
        page.wait_for_function("([s, t]) => { const e = document.querySelector(s); return !!e && e.innerText.includes(t); }", arg=[selector, text], timeout=timeout)
        return True
    except Exception:
        return False


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, has_touch=True, is_mobile=True,
                              accept_downloads=True, locale="pt-BR", timezone_id="America/Sao_Paulo")
    page = ctx.new_page()
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type in ("error",) else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

    print("1. primeira abertura e assistente")
    page.goto(BASE)
    page.wait_for_selector(".modal", timeout=15000)
    check("Bem-vindo" in page.inner_text(".modal-head"), "assistente de primeiro uso abre")
    page.screenshot(path=str(SHOTS / "01-assistente.png"))
    page.fill("#f-account_name", "Nubank")
    page.fill("#f-opening_balance", "1.000,00")
    page.click(".modal button[type=submit]")
    page.wait_for_selector(".modal", state="detached")
    check(True, "assistente concluído")

    print("2. lançamento rápido pelo botão +")
    page.click("#fab")
    page.wait_for_selector("#q-amount")
    page.fill("#q-amount", "45,90")
    page.fill("#q-desc", "Mercado do bairro")
    page.click(".qchip >> text=Mercado")
    page.screenshot(path=str(SHOTS / "02-lancamento-rapido.png"))
    page.click(".modal button[type=submit]")
    page.wait_for_selector(".modal", state="detached")
    page.wait_for_selector("#toasts .toast")
    check("Lançamento salvo" in page.inner_text("#toasts"), "toast de lançamento salvo")

    page.click("#fab")
    page.wait_for_selector("#q-amount")
    page.click(".modal .segmented button >> text=Entrada")
    page.fill("#q-amount", "3500")
    page.fill("#q-desc", "Salário")
    page.click(".modal button[type=submit]")
    page.wait_for_selector(".modal", state="detached")

    page.wait_for_selector(".hero")
    check(eventually(page, ".hero", "4.454,10"), f"patrimônio = 1000 + 3500 - 45,90 = 4.454,10 (veio: {page.inner_text('.hero')!r})")
    page.screenshot(path=str(SHOTS / "03-visao-geral.png"), full_page=True)
    check("Seus dados só estão neste aparelho" in page.inner_text("#view"), "aviso para salvar uma cópia aparece")

    print("3. todas as telas abrem sem erro")
    for hashname, title in [("mes", "Mês"), ("conc", "Concilia"), ("fixas", "Fixas"), ("invest", "Investimentos"), ("relat", "Relatórios"), ("config", "Configurações")]:
        page.goto(f"{BASE}/#/{hashname}")
        page.wait_for_selector("#view > *:not(.loading)")
        page.wait_for_timeout(300)
        body = page.inner_text("#view")
        check("Algo deu errado" not in body, f"tela {hashname} abre")
        page.screenshot(path=str(SHOTS / f"04-{hashname}.png"), full_page=True)

    print("4. persistência: recarregar mantém os dados")
    page.goto(f"{BASE}/#/mes")
    page.reload()
    page.wait_for_selector("#view > *:not(.loading)")
    page.wait_for_timeout(300)
    check(eventually(page, "#view", "Mercado do bairro"), "lançamento continua depois de recarregar")

    print("5. backup por arquivo")
    page.goto(f"{BASE}/#/config")
    page.wait_for_selector("text=Seus dados e backups")
    with page.expect_download() as dl:
        page.click("button:has-text('Salvar cópia atual')")
    d = dl.value
    path = SHOTS / d.suggested_filename
    d.save_as(path)
    check(d.suggested_filename.startswith("financas-backup-") and d.suggested_filename.endswith(".db"), f"nome do backup: {d.suggested_filename}")
    check(path.read_bytes().startswith(b"SQLite format 3\x00"), "backup é um banco SQLite")
    page.wait_for_timeout(300)
    page.reload()
    page.wait_for_selector("text=Cópia fora do aparelho")
    check("Em dia" in page.inner_text("#view"), "cópia fora do aparelho marcada como em dia")
    page.screenshot(path=str(SHOTS / "05-config-depois-backup.png"), full_page=True)

    print("6. relatório em Excel e CSV")
    page.goto(f"{BASE}/#/relat")
    page.wait_for_selector("text=Excel")
    with page.expect_download() as dl:
        page.click("button:has-text('Excel')")
    x = dl.value
    xp = SHOTS / x.suggested_filename
    x.save_as(xp)
    check(xp.read_bytes()[:2] == b"PK", f"xlsx válido ({x.suggested_filename})")
    with page.expect_download() as dl:
        page.click("button:has-text('CSV')")
    check(dl.value.suggested_filename.endswith(".csv"), "csv baixa")

    print("7. resetar e restaurar do arquivo")
    page.goto(f"{BASE}/#/config")
    page.wait_for_selector("text=Resetar dados")
    page.click("button:has-text('Resetar dados')")
    page.wait_for_selector("#f-confirm")
    page.fill("#f-confirm", "RESETAR")
    page.click(".modal button[type=submit]")
    page.wait_for_selector(".modal", state="detached")
    page.goto(f"{BASE}/#/visao")
    page.wait_for_selector("#view > *:not(.loading)")
    page.wait_for_timeout(500)
    check("Bem-vindo" in page.inner_text("body"), "app voltou ao estado de novo (assistente abre de novo)")
    if page.query_selector(".modal"):
        page.click(".modal .icon-btn[title=Fechar]")
        page.wait_for_selector(".modal", state="detached")
    page.goto(f"{BASE}/#/config")
    page.wait_for_selector("text=Restaurar de um arquivo")
    page.set_input_files("input[type=file]", str(path))
    page.wait_for_selector(".modal")
    page.click(".modal button.btn.danger, .modal button.btn.primary")
    page.wait_for_selector(".modal", state="detached")
    page.goto(f"{BASE}/#/mes")
    page.wait_for_selector("#view > *:not(.loading)")
    page.wait_for_timeout(400)
    check(eventually(page, "#view", "Mercado do bairro"), "dados voltaram depois de restaurar o arquivo")

    browser.close()

print()
real_errors = [e for e in errors if "favicon" not in e.lower()]
for e in real_errors:
    print("  erro no navegador:", e)
if real_errors:
    failures.append(f"{len(real_errors)} erro(s) no console do navegador")
print("RESULTADO:", "FALHOU" if failures else "tudo certo")
sys.exit(1 if failures else 0)
