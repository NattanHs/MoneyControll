"""Testa o worker.js de verdade (mensagens, gravação no IndexedDB, volta dos dados ao reabrir),
usando o Pyodide de mentira (tests/fake_pyodide.js) e o Python verdadeiro no servidor de teste.

Uso:  python tests/e2e_worker.py
"""
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8792
BASE = f"http://127.0.0.1:{PORT}"
DATA = Path("/tmp/mc-fake-dados")
failures = []


def check(cond, msg):
    print(("  ok   " if cond else "  FALHA ") + msg)
    if not cond:
        failures.append(msg)


def start_server():
    shutil.rmtree(DATA, ignore_errors=True)
    proc = subprocess.Popen([sys.executable, str(ROOT / "tests" / "dev_server.py"), "--port", str(PORT), "--data", str(DATA), "--fake"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(50):
        try:
            urllib.request.urlopen(BASE + "/", timeout=1)
            return proc
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("servidor de teste não subiu")


def idb_files(page):
    return page.evaluate("""() => new Promise((res, rej) => {
      const r = indexedDB.open('moneycontrol', 1);
      r.onsuccess = () => {
        const db = r.result; const out = {};
        const c = db.transaction('files').objectStore('files').openCursor();
        c.onsuccess = () => { const cur = c.result; if (cur) { out[cur.key] = cur.value.length; cur.continue(); } else { db.close(); res(out); } };
      };
      r.onerror = () => rej(r.error);
    })""")


def eventually(page, selector, text, timeout=6000):
    """Espera o texto aparecer (a tela redesenha depois de cada chamada); devolve True/False."""
    try:
        page.wait_for_function("([s, t]) => { const e = document.querySelector(s); return !!e && e.innerText.includes(t); }", arg=[selector, text], timeout=timeout)
        return True
    except Exception:
        return False


errors = []
srv = start_server()
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, accept_downloads=True, locale="pt-BR", timezone_id="America/Sao_Paulo")
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)

        print("1. primeira abertura pelo worker")
        page.goto(BASE)
        page.wait_for_selector(".modal", timeout=20000)
        check("Bem-vindo" in page.inner_text(".modal-head"), "worker iniciou e a tela abriu o assistente")
        page.fill("#f-account_name", "Itaú")
        page.fill("#f-opening_balance", "500")
        page.click(".modal button[type=submit]")
        page.wait_for_selector(".modal", state="detached")
        page.click("#fab")
        page.wait_for_selector("#q-amount")
        page.fill("#q-amount", "120,50")
        page.fill("#q-desc", "Farmácia")
        page.click(".modal button[type=submit]")
        page.wait_for_selector(".modal", state="detached")
        page.wait_for_selector("#toasts .toast")
        files = idb_files(page)
        check("/data/financas.db" in files and files["/data/financas.db"] > 4096, f"banco gravado no IndexedDB ({files.get('/data/financas.db')} bytes)")
        page.wait_for_selector(".hero")
        check(eventually(page, ".hero", "379,50"), f"saldo 500 − 120,50 = 379,50 (veio {page.inner_text('.hero')!r})")

        print("2. fechar tudo, 'Python' novo e vazio, reabrir: os dados voltam do IndexedDB")
        page.close()
        srv.terminate(); srv.wait()
        srv = start_server()                       # servidor novo com banco vazio = memória do Pyodide zerada
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.goto(f"{BASE}/#/mes")
        page.wait_for_selector("#view > *:not(.loading):not(.boot)", timeout=20000)
        check(eventually(page, "#view", "Farmácia"), "lançamento voltou depois de reabrir")
        check(not page.query_selector(".modal"), "assistente não reabre (conta já existe)")

        print("3. backup, reset e restauração passando pelo worker")
        page.goto(f"{BASE}/#/config")
        page.wait_for_selector("text=Seus dados e backups")
        with page.expect_download() as dl:
            page.click("button:has-text('Salvar cópia atual')")
        path = Path("/tmp/mc-fake-backup.db")
        dl.value.save_as(path)
        check(path.read_bytes().startswith(b"SQLite format 3\x00"), "arquivo de backup veio pelo worker")
        files = idb_files(page)
        check(any("/backups/" in k for k in files), f"backups automáticos também ficam no IndexedDB ({[k for k in files if '/backups/' in k]})")
        page.click("button:has-text('Resetar dados')")
        page.wait_for_selector("#f-confirm")
        page.fill("#f-confirm", "RESETAR")
        page.check("#f-wipe_backups")
        page.click(".modal button[type=submit]")
        page.wait_for_selector(".modal", state="detached")
        page.wait_for_timeout(400)
        files = idb_files(page)
        check(not any("/backups/" in k for k in files), "backups apagados do IndexedDB junto com o reset")
        page.goto(f"{BASE}/#/config")
        page.wait_for_selector("text=Restaurar de um arquivo")
        page.set_input_files("input[type=file]", str(path))
        page.wait_for_selector(".modal")
        page.click(".modal button.btn.danger, .modal button.btn.primary")
        page.wait_for_selector(".modal", state="detached")
        page.goto(f"{BASE}/#/mes")
        page.wait_for_selector("#view > *:not(.loading):not(.boot)")
        page.wait_for_timeout(300)
        check(eventually(page, "#view", "Farmácia"), f"dados voltaram depois de restaurar (veio {page.inner_text('#view')[:200]!r})")

        print("4. falha ao iniciar mostra mensagem e botão para tentar de novo")
        ctx2 = browser.new_context(viewport={"width": 390, "height": 844}, service_workers="block")
        ctx2.route("**/py/files.json", lambda route: route.fulfill(status=404, body="x"))
        page2 = ctx2.new_page()
        page2.goto(BASE)
        page2.wait_for_selector("text=Não foi possível iniciar o app", timeout=20000)
        check("Tentar de novo" in page2.inner_text("#view"), "erro de início aparece com botão")
        browser.close()
finally:
    srv.terminate()

real = [e for e in errors if "favicon" not in e.lower() and "404" not in e]
for e in real:
    print("  erro no navegador:", e)
if real:
    failures.append("erros no console")
print("RESULTADO:", "FALHOU" if failures else "tudo certo")
sys.exit(1 if failures else 0)
