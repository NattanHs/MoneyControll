"""Servidor de teste: entrega a pasta site/ e responde as chamadas da interface com o mesmo Python do app.

Só serve para testar a interface aqui, sem Pyodide. No celular quem faz esse papel é o worker.js.
Uso:  python tests/dev_server.py --port 8791 --data /tmp/dados
"""
import argparse
import base64
import json
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
sys.path.insert(0, str(ROOT / "site" / "py"))

from financas import api  # noqa: E402


FAKE = {"on": False, "data": None}


def _files_of(data_dir: Path) -> dict:
    out = {}
    for p in data_dir.rglob("*"):
        if p.is_file():
            out["/data/" + str(p.relative_to(data_dir)).replace("\\", "/")] = base64.b64encode(p.read_bytes()).decode()
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, body: bytes, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        if self.path == "/__fake/dispatch":      # "Pyodide de mentira": roda o Python de verdade aqui e devolve também os arquivos de dados
            raw = api.dispatch(data["method"], data["url"], data.get("body"), data["today"])
            res = json.loads(raw)
            out = None
            if res.get("file"):
                out = {"path": res["file"]["path"], "b64": base64.b64encode(Path(res["file"]["path"]).read_bytes()).decode()}
            return self._send(200, json.dumps({"raw": raw, "files": _files_of(FAKE["data"]), "out": out}).encode(), "application/json")
        if self.path == "/__fake/restore":       # o que o worker recuperou do IndexedDB vira o banco do servidor
            import shutil
            shutil.rmtree(FAKE["data"], ignore_errors=True)
            FAKE["data"].mkdir(parents=True)
            for path, b64 in data["files"].items():
                target = FAKE["data"] / path[len("/data/"):]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(base64.b64decode(b64))
            api.start(str(FAKE["data"] / "financas.db"))
            api._state["backup_day"] = None
            return self._send(200, b"{}", "application/json")
        if self.path != "/__api":
            return self._send(404, b"{}", "application/json")
        res = json.loads(api.dispatch(data["method"], data["path"], data.get("body"), data["today"]))
        if res.get("file"):
            f = res["file"]
            res["file"] = {"name": f["name"], "ctype": f["ctype"], "b64": base64.b64encode(Path(f["path"]).read_bytes()).decode()}
        self._send(200, json.dumps(res).encode(), "application/json")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", ""):
            path = "/index.html"
        if path == "/pyodide/pyodide.js" and FAKE["on"]:
            return self._send(200, (Path(__file__).parent / "fake_pyodide.js").read_bytes(), "application/javascript")
        if path == "/py/files.json":
            files = sorted(str(p.relative_to(SITE / "py")).replace("\\", "/") for p in (SITE / "py").rglob("*.py"))
            return self._send(200, json.dumps(files).encode(), "application/json")
        target = (SITE / path.lstrip("/")).resolve()
        if SITE.resolve() not in target.parents or not target.is_file():
            return self._send(404, b"nao encontrado", "text/plain")
        body = target.read_bytes()
        if path == "/index.html" and not FAKE["on"]:
            body = body.replace(b"<script src=\"backend.js\">", b"<script>window.MC_DEV_BACKEND='/__api'</script>\n  <script src=\"backend.js\">")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, body, ctype)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--data", default=str(ROOT / "dados-teste"))
    ap.add_argument("--fake", action="store_true", help="usa o worker.js de verdade com um Pyodide de mentira")
    ap.add_argument("--site", default=None, help="pasta a servir no lugar de site/ (ex.: dist, para testar o service worker)")
    a = ap.parse_args()
    if a.site:
        SITE = Path(a.site).resolve()
    FAKE["on"], FAKE["data"] = a.fake, Path(a.data)
    api.start(str(Path(a.data) / "financas.db"))
    print(f"http://127.0.0.1:{a.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()
