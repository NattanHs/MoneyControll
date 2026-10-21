"""Teste de ponta a ponta da sincronização com o Google Drive, com um Drive de mentira.

Sobe dois servidores de teste (o "celular" e o "PC", cada um com o seu banco) e um Google Drive simulado (Playwright
intercepta as chamadas a googleapis.com e accounts.google.com). Confere: primeiro envio, segundo aparelho baixando,
edição no PC chegando ao celular, conflito (as duas saídas), login expirado e retorno do login do Google.

Uso:  python tests/e2e_sync.py
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT_A, PORT_B = 8796, 8797
BASE_A, BASE_B = f"http://127.0.0.1:{PORT_A}", f"http://127.0.0.1:{PORT_B}"
failures = []
console_errors = []


def check(cond, msg):
    print(("  ok   " if cond else "  FALHA ") + msg)
    if not cond:
        failures.append(msg)
        if "drive" in globals():
            for f in drive.files.values():
                print("         [drive]", f["id"], f["name"], len(f["bytes"]), f["bytes"][:16])


PUMP = []   # uma página qualquer: esperar PELO PLAYWRIGHT (e não com time.sleep) é o que deixa o Drive de mentira responder


def wait_for(cond, timeout=20, step=0.25):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if cond():
                return True
        except Exception:
            pass
        if PUMP:
            PUMP[0].wait_for_timeout(int(step * 1000))
        else:
            time.sleep(step)
    return False


CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "authorization,content-type",
        "Access-Control-Allow-Methods": "GET,POST,PATCH,DELETE,OPTIONS"}


class FakeDrive:
    """Só o que o app usa: listar, baixar, criar (multipart), atualizar, copiar e apagar na pasta de dados do app."""

    def __init__(self):
        self.files = {}
        self.n = 0
        self.block = set()        # aparelhos "sem internet"
        self.bad_tokens = {"bad"}
        self.who_of = {}

    def _meta(self, f):
        return {"id": f["id"], "name": f["name"], "md5Checksum": hashlib.md5(f["bytes"]).hexdigest(),
                "modifiedTime": "2026-10-21T12:00:00.000Z", "size": str(len(f["bytes"]))}

    def _new(self, name, data):
        self.n += 1
        f = {"id": f"f{self.n}", "name": name, "bytes": data}
        self.files[f["id"]] = f
        return f

    def named(self, name):
        return [f for f in self.files.values() if f["name"] == name]

    def handler(self, who):
        def handle(route, request):
            if os.environ.get("SYNC_DEBUG"):
                print(f"   [{time.strftime('%H:%M:%S')}] {who} {request.method} {urlparse(request.url).path}", flush=True)
            if request.method == "OPTIONS":
                return route.fulfill(status=204, headers=CORS)
            if who in self.block:
                return route.abort("failed")
            token = (request.headers.get("authorization") or "").replace("Bearer ", "")
            if token in self.bad_tokens:
                return route.fulfill(status=401, headers=CORS, content_type="application/json", body=json.dumps({"error": {"message": "Invalid Credentials"}}))
            u = urlparse(request.url)
            path, q = u.path, parse_qs(u.query)
            send = lambda obj, status=200: route.fulfill(status=status, headers=CORS, content_type="application/json", body=json.dumps(obj))
            if request.method == "GET" and path == "/drive/v3/files":
                mm = re.search(r"name (=|contains) '([^']+)'", unquote(q["q"][0]))
                if mm.group(1) == "=":
                    found = self.named(mm.group(2))
                else:
                    found = [f for f in self.files.values() if mm.group(2) in f["name"]]
                return send({"files": [self._meta(f) for f in found]})
            m = re.fullmatch(r"/drive/v3/files/([^/]+)", path)
            if m and request.method == "GET" and q.get("alt") == ["media"]:
                return route.fulfill(status=200, headers=CORS, content_type="application/octet-stream", body=self.files[m.group(1)]["bytes"])
            if m and request.method == "DELETE":
                self.files.pop(m.group(1), None)
                return route.fulfill(status=204, headers=CORS)
            m = re.fullmatch(r"/drive/v3/files/([^/]+)/copy", path)
            if m and request.method == "POST":
                body = json.loads(request.post_data)
                return send(self._meta(self._new(body["name"], self.files[m.group(1)]["bytes"])))
            if path == "/upload/drive/v3/files" and request.method == "POST":
                boundary = request.headers["content-type"].split("boundary=")[1]
                parts = request.post_data_buffer.split(("--" + boundary).encode())
                meta = json.loads(parts[1].split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n"))
                content = parts[2].split(b"\r\n\r\n", 1)[1]
                if content.endswith(b"\r\n"):
                    content = content[:-2]
                assert meta.get("parents") == ["appDataFolder"], meta
                return send(self._meta(self._new(meta["name"], content)))
            m = re.fullmatch(r"/upload/drive/v3/files/([^/]+)", path)
            if m and request.method == "PATCH":
                self.files[m.group(1)]["bytes"] = request.post_data_buffer
                return send(self._meta(self.files[m.group(1)]))
            return route.fulfill(status=404, headers=CORS, body="?")
        return handle

    @staticmethod
    def _rows(data):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.db"
            p.write_bytes(data)
            con = sqlite3.connect(p)
            try:
                return sorted(r[0] for r in con.execute("SELECT description FROM transactions"))
            finally:
                con.close()

    def descriptions(self, name="financas.db"):
        """Descrições dos lançamentos que estão no arquivo do Drive."""
        f = self.named(name)
        return self._rows(f[0]["bytes"]) if f else None

    def previous(self):
        """Conteúdo (descrições) de cada cópia 'financas-anterior-*' guardada no Drive."""
        return [self._rows(f["bytes"]) for f in self.files.values() if f["name"].startswith("financas-anterior-")]


def start_server(port, data):
    shutil.rmtree(data, ignore_errors=True)
    data.mkdir(parents=True)
    proc = subprocess.Popen([sys.executable, str(ROOT / "tests" / "dev_server.py"), "--port", str(port), "--data", str(data)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    proc.stdout.readline()
    for _ in range(100):   # o servidor imprime o endereço antes de abrir a porta: espera abrir
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1).read()
            break
        except Exception:
            time.sleep(0.1)
    return proc


def add_tx(page, amount, desc):
    page.click("#fab")
    page.wait_for_selector("#q-amount")
    page.fill("#q-amount", amount)
    page.fill("#q-desc", desc)
    page.click(".qchip >> text=Mercado")
    page.click(".modal button[type=submit]")
    page.wait_for_selector(".modal", state="detached")


def status(page):
    return page.evaluate("Sync.status()")


def mes_text(page, base):
    page.goto(f"{base}/#/mes")
    page.wait_for_selector("#view > *:not(.loading)")
    page.wait_for_timeout(500)
    return page.inner_text("#view")


def new_device(browser, base, who, drive, token):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True, locale="pt-BR", timezone_id="America/Sao_Paulo")
    ctx.route("https://www.googleapis.com/**", drive.handler(who))
    ctx.route("https://oauth2.googleapis.com/**", lambda r, req: r.fulfill(status=200, headers=CORS, body="{}"))
    # o aparelho já "entrou" no Google: guarda um token válido (o login em si é testado mais abaixo)
    ctx.add_init_script("""if (!localStorage.getItem('mc_sync')) localStorage.setItem('mc_sync', JSON.stringify(
        { clientId: 'teste.apps.googleusercontent.com', token: '%s', expires: Date.now() + 3600000 }));""" % token)
    page = ctx.new_page()
    page.on("console", lambda m: console_errors.append(f"{who}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(f"{who} pageerror: {e}"))
    return ctx, page


drive = FakeDrive()
procs = [start_server(PORT_A, Path("/tmp/mc-sync-a")), start_server(PORT_B, Path("/tmp/mc-sync-b"))]
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()

        print("1. celular: primeiro uso envia para o Drive")
        ctxA, A = new_device(browser, BASE_A, "A", drive, "tkA")
        PUMP.append(A)
        A.goto(BASE_A)
        A.wait_for_selector(".modal", timeout=20000)
        A.fill("#f-account_name", "Nubank")
        A.fill("#f-opening_balance", "1.000,00")
        A.click(".modal button[type=submit]")
        A.wait_for_selector(".modal", state="detached")
        add_tx(A, "45,90", "Mercado do bairro")
        check(wait_for(lambda: drive.descriptions() == ["Mercado do bairro"]), f"Drive recebeu o lançamento do celular (tem: {drive.descriptions()})")
        check(wait_for(lambda: not status(A)["dirty"]), "celular fica 'em dia' depois de enviar")

        print("2. PC: primeira vez, banco vazio baixa sozinho e dispensa o assistente")
        ctxB, B = new_device(browser, BASE_B, "B", drive, "tkB")
        B.goto(f"{BASE_B}/#/mes")
        check(wait_for(lambda: "Mercado do bairro" in B.inner_text("body"), timeout=20), "PC mostra o lançamento vindo do Drive")
        check(B.query_selector(".modal") is None, "assistente de primeiro uso não aparece no PC")
        check(not status(B)["dirty"] and status(B)["lastSync"], "PC registra a sincronização")

        print("3. PC lança uma conferência e o celular recebe")
        add_tx(B, "10", "Conferencia PC")
        check(wait_for(lambda: "Conferencia PC" in (drive.descriptions() or [])), "Drive recebeu o lançamento do PC")
        A.evaluate("Sync.auto({force:true})")
        check(wait_for(lambda: "Conferencia PC" in mes_text(A, BASE_A), timeout=15), "celular recebeu o lançamento do PC")

        print("4. conflito: celular sem internet lança, PC lança, celular volta -> 'Manter os daqui'")
        drive.block.add("A")
        add_tx(A, "20", "Lancamento A offline")
        check(wait_for(lambda: status(A)["lastError"], timeout=12), "celular guarda o erro de rede e continua com alterações pendentes")
        check(status(A)["dirty"], "alterações do celular continuam pendentes")
        add_tx(B, "30", "Lancamento B online")
        check(wait_for(lambda: "Lancamento B online" in (drive.descriptions() or [])), "Drive recebeu o do PC")
        drive.block.discard("A")
        A.evaluate("Sync.auto({force:true})")
        check(wait_for(lambda: A.query_selector(".modal-head") is not None and "Dados diferentes" in A.inner_text(".modal-head")), "celular avisa o conflito")
        A.click("button:has-text('Manter os daqui')")
        check(wait_for(lambda: "Lancamento A offline" in (drive.descriptions() or []) and "Lancamento B online" not in drive.descriptions()), "Drive passou a ter os dados do celular")
        check(any("Lancamento B online" in rows for rows in drive.previous()), "a versão que o Drive tinha ficou guardada no próprio Drive")
        B.evaluate("Sync.auto({force:true})")
        check(wait_for(lambda: "Lancamento A offline" in mes_text(B, BASE_B) and "Lancamento B online" not in B.inner_text("#view"), timeout=15), "PC recebeu os dados escolhidos")

        print("5. conflito: 'Usar os do Drive'")
        drive.block.add("A")
        add_tx(A, "5", "A2 so no celular")
        check(wait_for(lambda: status(A)["lastError"], timeout=12), "celular segue sem internet")
        add_tx(B, "6", "B2 no PC")
        check(wait_for(lambda: "B2 no PC" in (drive.descriptions() or [])), "Drive recebeu o B2")
        drive.block.discard("A")
        A.evaluate("Sync.auto({force:true})")
        check(wait_for(lambda: A.query_selector(".modal-head") is not None and "Dados diferentes" in A.inner_text(".modal-head")), "celular avisa o conflito de novo")
        A.click("button:has-text('Usar os do Drive')")
        check(wait_for(lambda: "B2 no PC" in mes_text(A, BASE_A) and "A2 so no celular" not in A.inner_text("#view"), timeout=15), "celular ficou com os dados do Drive")
        A.goto(f"{BASE_A}/#/config")
        A.wait_for_selector("text=Seus dados e backups")
        check("Antes de restaurar" in A.inner_text("#view"), "o app guardou uma cópia de segurança dos dados do celular antes de substituir")
        check(drive.descriptions().count("A2 so no celular") == 0, "o Drive não foi alterado nessa escolha")

        print("5b. PC desatualizado volta: recebe, nunca envia a versão velha por cima")
        drive.block.add("B")
        add_tx(A, "7", "A3 enquanto o PC estava offline")
        check(wait_for(lambda: "A3 enquanto o PC estava offline" in (drive.descriptions() or [])), "Drive recebeu o A3")
        drive.block.discard("B")
        B.evaluate("Sync.auto({force:true})")
        check(wait_for(lambda: "A3 enquanto o PC estava offline" in mes_text(B, BASE_B), timeout=15), "PC recebeu o A3")
        check("A3 enquanto o PC estava offline" in drive.descriptions(), "o Drive continua com o A3 (a versão velha do PC não o cobriu)")

        print("5c. restaurar um arquivo MAIS ANTIGO: avisa, só com confirmação, e o Drive guarda uma cópia antes")
        A.goto(f"{BASE_A}/#/config")
        A.wait_for_selector("text=Seus dados e backups")
        A.click("button:has-text('Criar backup agora')")
        A.wait_for_selector(".list-row:has-text('Manual')")
        add_tx(A, "9", "Depois do backup manual")
        check(wait_for(lambda: "Depois do backup manual" in (drive.descriptions() or [])), "Drive recebeu o lançamento feito depois do backup")
        A.goto(f"{BASE_A}/#/config")
        A.wait_for_selector(".list-row:has-text('Manual')")
        A.click(".list-row:has-text('Manual') button:has-text('Restaurar')")
        A.wait_for_selector(".modal")
        modal_text = A.inner_text(".modal")
        check("MAIS ANTIGO" in modal_text, "o app avisa que o arquivo é mais antigo")
        check("Arquivo:" in modal_text and "Atual:" in modal_text, "mostra a contagem de lançamentos do arquivo e dos dados atuais")
        A.click(".modal button:has-text('Cancelar')")
        A.wait_for_selector(".modal", state="detached")
        check("Depois do backup manual" in mes_text(A, BASE_A), "cancelar não mexeu em nada")
        A.goto(f"{BASE_A}/#/config")
        A.wait_for_selector(".list-row:has-text('Manual')")
        A.click(".list-row:has-text('Manual') button:has-text('Restaurar')")
        A.wait_for_selector(".modal")
        A.click(".modal button:has-text('Restaurar mesmo assim')")
        A.wait_for_selector(".modal", state="detached")
        check("Depois do backup manual" not in mes_text(A, BASE_A), "restauração aplicada no celular por decisão da pessoa")
        check(wait_for(lambda: "Depois do backup manual" not in (drive.descriptions() or []), timeout=15), "a decisão deliberada vale também no Drive")
        check(any("Depois do backup manual" in rows for rows in drive.previous()), "o Drive guardou uma cópia da versão que foi substituída")
        B.evaluate("Sync.auto({force:true})")
        check(wait_for(lambda: "Depois do backup manual" not in mes_text(B, BASE_B), timeout=15), "o PC recebeu a versão restaurada sem acusar conflito")
        check(B.query_selector(".modal") is None, "sem janela de conflito no PC")

        print("6. sessão do Google expirada: aviso e botão Conectar")
        add_tx_page = A
        A.evaluate("localStorage.setItem('mc_sync', JSON.stringify({...JSON.parse(localStorage.getItem('mc_sync')), expires: Date.now() - 1000, dirty: true}))")
        A.reload()
        A.wait_for_selector("#view > *:not(.loading)")
        A.goto(f"{BASE_A}/#/visao")
        check(wait_for(lambda: "Sincronização pausada" in A.inner_text("#view")), "aviso de sincronização pausada aparece")
        A.goto(f"{BASE_A}/#/config")
        A.wait_for_selector("text=Sincronizar com o Google Drive")
        check(A.query_selector("button:has-text('Conectar ao Google Drive')") is not None, "tela de configurações oferece 'Conectar'")

        print("7. login do Google: ida e volta")
        seen = {}

        def oauth(route, request):
            q = parse_qs(urlparse(request.url).query)
            seen.update({k: v[0] for k, v in q.items()})
            route.fulfill(status=302, headers={"Location": f"{q['redirect_uri'][0]}#access_token=newtoken&token_type=Bearer&expires_in=3599&state={q['state'][0]}&scope=x"})
        ctxA.route("https://accounts.google.com/**", oauth)
        A.click("button:has-text('Conectar ao Google Drive')")
        check(wait_for(lambda: seen.get("client_id") == "teste.apps.googleusercontent.com"), "pediu o login com o ID do cliente")
        check(seen.get("scope") == "https://www.googleapis.com/auth/drive.appdata", f"pede só a pasta de dados do app ({seen.get('scope')})")
        check(seen.get("response_type") == "token" and seen.get("redirect_uri") == BASE_A + "/", f"endereço de retorno = o do app ({seen.get('redirect_uri')})")
        check(wait_for(lambda: A.evaluate("JSON.parse(localStorage.getItem('mc_sync')).token") == "newtoken"), "token novo guardado")
        check(wait_for(lambda: A.evaluate("location.hash") == "#/config"), "endereço limpo, de volta nas configurações")
        check(wait_for(lambda: status(A)["connected"]), "app mostra como conectado")
        check(wait_for(lambda: not status(A)["dirty"] and A.evaluate("Sync.status().lastSync") != ""), "sincronizou logo depois de conectar")

        print("8. retorno de login forjado (state errado) é recusado")
        A.goto(f"{BASE_A}/#access_token=zzz&state=forjado&expires_in=3599")
        A.reload()
        A.wait_for_selector("#view > *:not(.loading)")
        check(A.evaluate("JSON.parse(localStorage.getItem('mc_sync')).token") != "zzz", "token com state errado não é aceito")
        check(wait_for(lambda: "confirmar o login" in A.inner_text("#toasts")), "avisa que não confirmou o login")

        print("9. token recusado pelo Google (401) pede novo login")
        A.evaluate("localStorage.setItem('mc_sync', JSON.stringify({...JSON.parse(localStorage.getItem('mc_sync')), token: 'bad', expires: Date.now() + 3600000}))")
        r = A.evaluate("Sync.run().then(r => r.state)")
        check(r == "login" and not status(A)["connected"], f"401 vira pedido de login (state={r})")

        print("10. desconectar")
        A.goto(f"{BASE_A}/#/config")
        A.evaluate("localStorage.setItem('mc_sync', JSON.stringify({...JSON.parse(localStorage.getItem('mc_sync')), token: 'tkA', expires: Date.now() + 3600000}))")
        A.reload()
        A.wait_for_selector("button:has-text('Desconectar')")
        A.click("button:has-text('Desconectar')")
        A.wait_for_selector(".modal")
        A.click(".modal button.btn.primary")
        check(wait_for(lambda: not status(A)["connected"] and not status(A)["everConnected"]), "desconectou e esqueceu a sessão")
        check(drive.descriptions() is not None, "o arquivo do Drive continua lá")

        browser.close()
finally:
    for p in procs:
        p.terminate()

print()
real = [e for e in console_errors if not re.search(r"Failed to load resource|ERR_FAILED|favicon", e)]
for e in real:
    print("  erro no navegador:", e)
if real:
    failures.append(f"{len(real)} erro(s) no console")
print("RESULTADO:", "FALHOU" if failures else "tudo certo")
sys.exit(1 if failures else 0)
