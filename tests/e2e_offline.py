"""Testa o service worker: depois de aberto uma vez, o app carrega mesmo sem rede.
Monta uma pasta dist de teste (sem baixar o Pyodide), com o Pyodide de mentira no lugar.

Uso:  python tests/e2e_offline.py
"""
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8794
BASE = f"http://127.0.0.1:{PORT}"
failures = []


def check(cond, msg):
    print(("  ok   " if cond else "  FALHA ") + msg)
    if not cond:
        failures.append(msg)


subprocess.run(["node", str(ROOT / "scripts" / "montar-site.mjs"), "--sem-pyodide"], check=True, stdout=subprocess.DEVNULL)
(ROOT / "dist" / "pyodide").mkdir(exist_ok=True)
shutil.copy(ROOT / "tests" / "fake_pyodide.js", ROOT / "dist" / "pyodide" / "pyodide.js")
# o pyodide de mentira entra na lista de arquivos guardados, como o de verdade entraria
import json
pre = json.loads((ROOT / "dist" / "precache.json").read_text())
pre["files"].append("pyodide/pyodide.js")
(ROOT / "dist" / "precache.json").write_text(json.dumps(pre))

DATA = Path("/tmp/mc-offline-dados")
shutil.rmtree(DATA, ignore_errors=True)
srv = subprocess.Popen([sys.executable, str(ROOT / "tests" / "dev_server.py"), "--port", str(PORT), "--data", str(DATA), "--fake", "--site", str(ROOT / "dist")],
                       stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
try:
    for _ in range(50):
        try:
            urllib.request.urlopen(BASE + "/", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 390, "height": 844})
        page = ctx.new_page()
        page.goto(BASE)
        page.wait_for_selector(".modal", timeout=20000)   # primeira vez: assistente
        page.evaluate("navigator.serviceWorker.ready")
        # espera o service worker terminar de guardar os arquivos
        ok = False
        for _ in range(60):
            n = page.evaluate("async () => { const ks = await caches.keys(); if (!ks.length) return 0; return (await (await caches.open(ks[0])).keys()).length; }")
            if n >= len(pre["files"]) - 1:
                ok = True
                break
            page.wait_for_timeout(250)
        check(ok, f"service worker guardou os {len(pre['files']) - 1} arquivos do app")
        keys = page.evaluate("async () => (await caches.keys())")
        check(len(keys) == 1 and keys[0] == "mc-" + pre["build"], f"cache com a versão da publicação ({keys})")

        print("sem rede (servidor desligado e navegador offline)")
        srv.terminate(); srv.wait()
        ctx.set_offline(True)
        page.reload()
        page.wait_for_selector("#view", timeout=15000)
        page.wait_for_timeout(2500)
        html = page.content()
        check("MoneyControl" in html and "style.css" in html, "a página abre offline (index.html veio do cache)")
        text = page.inner_text("#view")
        # o Pyodide de mentira precisa do servidor de teste; o que importa aqui é que o app e o worker carregaram do cache
        check("Iniciando" in text or "Carregando" in text or "Não foi possível iniciar" in text or "Abrindo" in text, f"worker.js carregou do cache e tentou iniciar ({text[:80]!r})")
        browser.close()
finally:
    srv.terminate()
    shutil.rmtree(ROOT / "dist", ignore_errors=True)

print("RESULTADO:", "FALHOU" if failures else "tudo certo")
sys.exit(1 if failures else 0)
