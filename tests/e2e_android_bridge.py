"""Testa o lado JavaScript do app Android com uma ponte window.MCAndroid de mentira:
salvar em Downloads, compartilhar e imprimir chamam a ponte com os dados certos.

Uso:  python tests/e2e_android_bridge.py
"""
import base64
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8795
BASE = f"http://127.0.0.1:{PORT}"
DATA = Path("/tmp/mc-android-dados")
failures = []

STUB = """
window.__calls = [];
window.MCAndroid = {
  saveToDownloads(name, mime, b64) { window.__calls.push(['downloads', name, mime, b64]); return ''; },
  share(name, mime, b64) { window.__calls.push(['share', name, mime, b64]); return ''; },
  print() { window.__calls.push(['print']); },
  toast(t) { window.__calls.push(['toast', t]); },
};
"""


def check(cond, msg):
    print(("  ok   " if cond else "  FALHA ") + msg)
    if not cond:
        failures.append(msg)


shutil.rmtree(DATA, ignore_errors=True)
srv = subprocess.Popen([sys.executable, str(ROOT / "tests" / "dev_server.py"), "--port", str(PORT), "--data", str(DATA)], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
errors = []
try:
    for _ in range(50):
        try:
            urllib.request.urlopen(BASE + "/", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, locale="pt-BR", timezone_id="America/Sao_Paulo")
        ctx.add_init_script(STUB)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE)
        page.wait_for_selector(".modal", timeout=15000)
        page.fill("#f-account_name", "Conta")
        page.click(".modal button[type=submit]")
        page.wait_for_selector(".modal", state="detached")

        print("salvar cópia em Downloads")
        page.goto(f"{BASE}/#/config")
        page.wait_for_selector("button:has-text('Salvar cópia atual')")
        page.click("button:has-text('Salvar cópia atual')")
        page.wait_for_selector(".modal:has-text('Salvar arquivo')")
        check("financas-backup-" in page.inner_text(".modal"), "pergunta onde salvar e mostra o nome do arquivo")
        page.click(".modal button:has-text('Salvar em Downloads')")
        page.wait_for_selector(".modal", state="detached")
        calls = page.evaluate("window.__calls")
        c = [x for x in calls if x[0] == "downloads"]
        check(len(c) == 1 and c[0][1].startswith("financas-backup-") and c[0][1].endswith(".db"), "ponte recebeu o nome do backup")
        check(base64.b64decode(c[0][3]).startswith(b"SQLite format 3\x00"), "ponte recebeu o banco SQLite em base64")
        page.wait_for_selector("#toasts .toast")
        check("Downloads" in page.inner_text("#toasts"), "aviso 'Salvo na pasta Downloads'")

        print("compartilhar o relatório em Excel")
        page.goto(f"{BASE}/#/relat")
        page.wait_for_selector("button:has-text('Excel')")
        page.click("button:has-text('Excel')")
        page.wait_for_selector(".modal:has-text('Salvar arquivo')")
        page.click(".modal button:has-text('Compartilhar')")
        page.wait_for_selector(".modal", state="detached")
        s = [x for x in page.evaluate("window.__calls") if x[0] == "share"]
        check(len(s) == 1 and s[0][1].endswith(".xlsx") and "spreadsheetml" in s[0][2], "ponte recebeu o xlsx com o tipo certo")
        check(base64.b64decode(s[0][3])[:2] == b"PK", "conteúdo do xlsx é um zip válido")

        print("fechar sem escolher não conta como salvo")
        page.click("button:has-text('CSV')")
        page.wait_for_selector(".modal:has-text('Salvar arquivo')")
        page.click(".modal .icon-btn[title=Fechar]")
        page.wait_for_selector(".modal", state="detached")
        check(len([x for x in page.evaluate("window.__calls") if x[0] in ("share", "downloads")]) == 2, "nada foi enviado à ponte ao cancelar")

        print("imprimir")
        page.click("button:has-text('Imprimir')")
        check(["print"] in page.evaluate("window.__calls"), "botão Imprimir chama a ponte do Android")
        check(page.evaluate("typeof navigator.serviceWorker") != "undefined", "navegador tem service worker, mas o app não registra dentro do APK")
        reg = page.evaluate("navigator.serviceWorker.getRegistrations().then(r => r.length)")
        check(reg == 0, "service worker não é registrado dentro do app Android")
        browser.close()
finally:
    srv.terminate()
real = [e for e in errors if "favicon" not in e.lower()]
for e in real:
    print("  erro:", e)
if real:
    failures.append("erros no navegador")
print("RESULTADO:", "FALHOU" if failures else "tudo certo")
sys.exit(1 if failures else 0)
