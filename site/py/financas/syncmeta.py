"""Linhagem das versões dos dados: a base da regra "nunca trocar dados novos por dados antigos".

Comparar data e hora entre aparelhos não é seguro (o relógio de um pode estar errado) e "mais novo" não quer dizer "tem tudo
que o outro tem". Por isso cada versão dos dados carrega, DENTRO do próprio banco (tabela sync_meta), a lista das versões
de que descende (`history`, a última é a versão atual, a "ponta"). Assim, para dois bancos A e B:

  * a ponta de B está no histórico de A  -> A descende de B: A é mais novo e pode substituir B;
  * a ponta de A está no histórico de B  -> B é mais novo e pode substituir A;
  * nenhum descende do outro             -> os dois mudaram por caminhos diferentes: é um conflito, e só uma pessoa decide.

A lista viaja junto com o arquivo (backup, Drive), então a conferência funciona mesmo importando um arquivo qualquer.
`dirty` marca que houve alterações aqui depois da última versão registrada; `rewind` marca que os dados foram
deliberadamente "voltados" (restauração manual) e que o Drive deve guardar uma cópia antes de ser substituído.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

HISTORY_MAX = 300   # se um aparelho ficar mais de 300 versões para trás, a linhagem se perde e o app pergunta (lado seguro)

RELATIONS = ("same", "file-older", "file-newer", "diverged", "unknown")


def read(conn) -> dict:
    rows = {r[0]: r[1] for r in conn.execute("SELECT key, value FROM sync_meta")}
    try:
        history = [str(v) for v in json.loads(rows.get("history") or "[]")]
    except (ValueError, TypeError):
        history = []
    return {"history": history, "dirty": rows.get("dirty") == "1", "rewind": rows.get("rewind") == "1"}


def _put(conn, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO sync_meta(key, value) VALUES (?, ?)", (key, value))


def write(conn, history: list[str], dirty: bool, rewind: bool = False) -> None:
    _put(conn, "history", json.dumps(history[-HISTORY_MAX:]))
    _put(conn, "dirty", "1" if dirty else "0")
    _put(conn, "rewind", "1" if rewind else "0")


def head(history: list[str]) -> str | None:
    return history[-1] if history else None


def mark_dirty(conn) -> None:
    if not read(conn)["dirty"]:
        _put(conn, "dirty", "1")


def relation(local_history: list[str], file_history: list[str]) -> str:
    """Como o ARQUIVO se compara com os dados daqui: same | file-older | file-newer | diverged | unknown."""
    lh, fh = head(local_history), head(file_history)
    if lh is None or fh is None:
        return "unknown"             # sem linhagem de um dos lados (banco antigo): não dá para saber qual é mais novo
    if lh == fh:
        return "same"
    if fh in local_history:
        return "file-older"          # o arquivo é um ancestral dos dados daqui
    if lh in file_history:
        return "file-newer"          # os dados daqui são um ancestral do arquivo
    return "diverged"


def stamp(conn, absorb: list[str] | None = None, force: bool = False) -> dict:
    """Registra uma nova versão se houver alterações pendentes (ou se for preciso absorver a linhagem de outro lado).

    `absorb` é a linhagem do arquivo que está no Drive: quando a pessoa escolhe "manter os daqui" num conflito, a nova versão
    passa a descender dele, e assim o Drive o aceita como uma atualização legítima (e os outros aparelhos o veem como mais novo)."""
    st = read(conn)
    history = list(st["history"])
    added = [v for v in (absorb or []) if v not in history]
    if not (st["dirty"] or not history or added or force):
        return {**st, "head": head(history), "stamped": False}
    history.extend(added)
    history.append(uuid.uuid4().hex)
    write(conn, history, dirty=False, rewind=False)
    return {"history": history, "dirty": False, "rewind": st["rewind"], "head": head(history), "stamped": True}


def state(conn) -> dict:
    st = read(conn)
    st["head"] = head(st["history"])
    st["has_data"] = has_data(conn)
    return st


def has_data(conn) -> bool:
    try:
        return bool(conn.execute(
            "SELECT (SELECT COUNT(*) FROM transactions) + (SELECT COUNT(*) FROM fixed_items) + "
            "(SELECT COUNT(*) FROM installments) + (SELECT COUNT(*) FROM asset_ops) + "
            "(SELECT COUNT(*) FROM fixed_income_moves)").fetchone()[0])
    except sqlite3.DatabaseError:
        return False


def _summary(conn) -> dict:
    try:
        n, last = conn.execute("SELECT COUNT(*), MAX(date) FROM transactions").fetchone()
    except sqlite3.DatabaseError:
        n, last = 0, None
    return {"tx_count": n or 0, "last_date": last}


def file_info(path: Path) -> dict:
    """Linhagem e um resumo de um arquivo de banco (backup, Drive), sem alterá-lo. Banco antigo = sem linhagem."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        try:
            st = read(conn)
        except sqlite3.DatabaseError:
            st = {"history": [], "dirty": False, "rewind": False}
        return {**st, "head": head(st["history"]), **_summary(conn)}
    finally:
        conn.close()


def compare(conn, path: Path) -> dict:
    """Compara um arquivo com os dados daqui (usado antes de restaurar e na sincronização)."""
    local = read(conn)
    info = file_info(path)
    return {
        "relation": relation(local["history"], info["history"]),
        "file": {"head": info["head"], "history": info["history"], "tx_count": info["tx_count"], "last_date": info["last_date"]},
        "local": {"head": head(local["history"]), "dirty": local["dirty"], "has_data": has_data(conn), **_summary(conn)},
    }
