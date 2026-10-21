"""Ponto de entrada da API no celular: recebe (método, caminho, corpo) e devolve a resposta em JSON.

Não há servidor HTTP. O navegador chama `dispatch()` dentro do próprio aparelho, e esta função
usa as mesmas rotas e a mesma lógica de cálculo da versão de computador.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import traceback
from pathlib import Path
from urllib.parse import urlparse

from . import backup, db, syncmeta
from .routes import ROUTES, Ctx

_state = {"db_path": None, "backup_day": None}

# chamadas que não são "o usuário alterou os dados": o que a sincronização e as restaurações fazem tratam do próprio estado
_NO_DIRTY = {"/api/backups", "/api/backups/restore", "/api/backups/restore-upload", "/api/backups/inspect",
             "/api/backups/inspect-upload", "/api/sync/state", "/api/sync/stamp", "/api/sync/adopt"}


def start(db_path: str) -> str:
    """Prepara o banco (cria as tabelas na primeira vez ou atualiza o de um backup antigo)."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _state["db_path"] = path
    with db.session(path):
        pass
    return "ok"


def _out_dir() -> Path:
    return Path(tempfile.gettempdir())


def _reply(status: int, body=None, file: dict | None = None) -> str:
    return json.dumps({"status": status, "body": body, "file": file}, ensure_ascii=False)


def _daily_backup(db_path: Path, today: str) -> None:
    """Cópia automática do dia: no máximo uma por dia. Falhar aqui nunca atrapalha o uso."""
    if _state["backup_day"] == today:
        return
    try:
        backup.auto_backup(db_path, today)
        # só dá o dia por feito quando a cópia existe (banco vazio não é copiado: tenta de novo na próxima chamada)
        if (backup.backup_dir(db_path) / f"auto-{today}.db").exists():
            _state["backup_day"] = today
    except Exception:  # noqa: BLE001
        traceback.print_exc()


def dispatch(method: str, url: str, body: str | None, today: str) -> str:
    """Executa uma chamada da interface. `today` é a data de hoje (AAAA-MM-DD) no relógio do aparelho."""
    db_path = _state["db_path"]
    if db_path is None:
        return _reply(500, {"error": "O banco de dados ainda não foi iniciado."})
    path = urlparse(url).path
    method = method.upper()
    for route_method, pattern, fn in ROUTES:
        match = pattern.match(path)
        if match and route_method == method:
            break
    else:
        return _reply(404, {"error": "Rota não encontrada."})
    try:
        data = json.loads(body) if body else {}
        if not isinstance(data, dict):
            raise ValueError("Corpo inválido.")
        _daily_backup(db_path, today)
        ctx = Ctx(db_path, today)
        with db.session(db_path) as conn:
            result = fn(conn, match, data, ctx)
            if method != "GET" and path not in _NO_DIRTY:
                syncmeta.mark_dirty(conn)   # há alterações aqui que ainda não viraram uma versão sincronizada
        if isinstance(result, dict) and "__file__" in result:
            out = _out_dir() / "mc-saida.bin"
            out.write_bytes(result["__file__"])
            return _reply(200, None, {"path": str(out), "name": result["name"],
                                      "ctype": result.get("ctype", "application/octet-stream")})
        return _reply(200, result)
    except (ValueError, sqlite3.IntegrityError, json.JSONDecodeError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "Dados inválidos."
        return _reply(400, {"error": message})
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return _reply(500, {"error": f"Erro interno: {exc}"})
