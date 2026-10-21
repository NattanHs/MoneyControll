"""Backups do banco: cópia diária automática, cópia manual e restauração."""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from . import db

AUTO_KEEP = 7       # cópias diárias mantidas (no celular o espaço é menor)
SAFETY_KEEP = 5     # cópias de segurança feitas antes de cada restauração
MANUAL_KEEP = 10
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.db$")
KINDS = {"auto": "Automático (diário)", "manual": "Manual", "antes": "Antes de restaurar"}
REQUIRED_TABLES = {"settings", "transactions", "fixed_items", "installments"}


def backup_dir(db_path) -> Path:
    path = Path(db_path).parent / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _snapshot(src, dest: Path) -> None:
    """Cópia consistente (API de backup do SQLite) de um banco ou conexão para um arquivo."""
    target = sqlite3.connect(str(dest))
    try:
        if isinstance(src, sqlite3.Connection):
            src.backup(target)
        else:
            source = sqlite3.connect(str(src))
            try:
                source.backup(target)
            finally:
                source.close()
    finally:
        target.close()


def _unique(folder: Path, prefix: str) -> Path:
    """Nome novo para uma cópia, sem nunca sobrescrever uma existente (duas no mesmo segundo)."""
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    path, n = folder / f"{prefix}-{stamp}.db", 1
    while path.exists():
        n += 1
        path = folder / f"{prefix}-{stamp}-{n}.db"
    return path


def _prune(folder: Path, prefix: str, keep: int) -> None:
    files = sorted(folder.glob(f"{prefix}-*.db"), key=lambda p: p.name, reverse=True)
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


def _has_data(db_path) -> bool:
    conn = sqlite3.connect(str(db_path))
    try:
        return bool(conn.execute(
            "SELECT (SELECT COUNT(*) FROM transactions) + (SELECT COUNT(*) FROM fixed_items) + "
            "(SELECT COUNT(*) FROM installments) + (SELECT COUNT(*) FROM asset_ops) + "
            "(SELECT COUNT(*) FROM fixed_income_moves)").fetchone()[0])
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.close()


def auto_backup(db_path, today: str) -> Path | None:
    """Cria a cópia do dia (uma por dia) e apaga as mais antigas. Não copia banco vazio."""
    db_path = Path(db_path)
    if not db_path.exists() or not _has_data(db_path):
        return None
    folder = backup_dir(db_path)
    dest = folder / f"auto-{today}.db"
    if dest.exists():
        return None
    _snapshot(db_path, dest)
    _prune(folder, "auto", AUTO_KEEP)
    return dest


def manual_backup(conn, db_path) -> Path:
    folder = backup_dir(db_path)
    dest = _unique(folder, "manual")
    _snapshot(conn, dest)
    _prune(folder, "manual", MANUAL_KEEP)
    return dest


def list_backups(db_path) -> list[dict]:
    out = []
    for p in backup_dir(db_path).glob("*.db"):
        kind = p.name.split("-", 1)[0]
        if kind not in KINDS:
            continue
        stat = p.stat()
        out.append({"name": p.name, "kind": kind, "kind_label": KINDS[kind], "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")})
    out.sort(key=lambda b: b["modified"] + b["name"], reverse=True)
    return out


def backup_path(db_path, name: str) -> Path:
    if not NAME_RE.fullmatch(str(name or "")):
        raise ValueError("Backup inválido.")
    path = backup_dir(db_path) / name
    if not path.is_file():
        raise ValueError("Backup não encontrado.")
    return path


def validate_backup(path: Path) -> None:
    """Confere se o arquivo é um banco deste aplicativo, íntegro."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("O arquivo de backup está corrompido.")
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        raise ValueError("Este arquivo não é um backup válido do aplicativo.") from None
    if not REQUIRED_TABLES <= tables:
        raise ValueError("Este arquivo não é um backup válido do aplicativo.")


def restore(conn, db_path, source: Path) -> dict:
    """Substitui os dados atuais pelos do backup. Antes, guarda uma cópia de segurança dos dados atuais."""
    validate_backup(source)
    folder = backup_dir(db_path)
    safety = _unique(folder, "antes")
    _snapshot(conn, safety)
    _prune(folder, "antes", SAFETY_KEEP)
    src = sqlite3.connect(str(source))
    try:
        src.backup(conn)
    finally:
        src.close()
    db.init(conn)  # aplica atualizações de estrutura caso o backup seja de uma versão antiga
    return {"safety": safety.name}


def restore_upload(conn, db_path, b64: str) -> dict:
    try:
        raw = base64.b64decode(str(b64 or ""), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Arquivo inválido.") from None
    if not raw.startswith(b"SQLite format 3\x00"):
        raise ValueError("Este arquivo não é um backup válido do aplicativo.")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "enviado.db"
        path.write_bytes(raw)
        return restore(conn, db_path, path)


# ---------------------------------------------------------------- cópia fora do computador

EXTERNAL_KEEP = 30     # cópias mantidas na pasta externa
STALE_DAYS = 7         # sem cópia externa há mais que isso: avisa
EXTERNAL_PREFIX = "MoneyControl"


def _ext_config_path(db_path) -> Path:
    return Path(db_path).parent / "backup_externo.json"


def _ext_load(db_path) -> dict:
    try:
        raw = json.loads(_ext_config_path(db_path).read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _ext_save(db_path, cfg: dict) -> None:
    path = _ext_config_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def external_wipe(db_path) -> None:
    try:
        _ext_config_path(db_path).unlink()
    except OSError:
        pass


def suggest_external_dirs() -> list[dict]:
    """Pastas que costumam ser sincronizadas com a nuvem e unidades removíveis, se existirem."""
    home = Path(os.path.expanduser("~"))
    found: list[tuple[str, Path]] = []
    for env in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if os.environ.get(env):
            found.append(("OneDrive", Path(os.environ[env])))
    for name, label in (("OneDrive", "OneDrive"), ("Dropbox", "Dropbox"), ("Google Drive", "Google Drive"), ("Meu Drive", "Google Drive"), ("iCloudDrive", "iCloud Drive")):
        found.append((label, home / name))
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        drive = Path(f"{letter}:\\")
        try:
            if os.name == "nt" and drive.exists():
                found.append((f"Unidade {letter}:", drive))
        except OSError:
            pass
    out, seen = [], set()
    for label, p in found:
        try:
            ok = p.is_dir()
        except OSError:
            ok = False
        key = str(p).lower()
        if ok and key not in seen:
            seen.add(key)
            out.append({"label": label, "path": str(p)})
    return out


def _check_writable(folder: Path) -> None:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".moneycontrol-teste"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        raise ValueError("Não consegui gravar nessa pasta. Confira se ela existe, se o pen drive está conectado e se você tem permissão.") from None


def set_external_dir(db_path, path: str) -> dict:
    text = str(path or "").strip().strip('"')
    if not text:
        raise ValueError("Informe a pasta onde as cópias serão guardadas.")
    folder = Path(os.path.expandvars(os.path.expanduser(text)))
    if not folder.is_absolute():
        raise ValueError("Informe o caminho completo da pasta (por exemplo C:\\Users\\voce\\OneDrive\\Backups).")
    try:
        same = folder.resolve() == backup_dir(db_path).resolve()
    except OSError:
        same = False
    if same:
        raise ValueError("Escolha uma pasta diferente da pasta de backups do aplicativo.")
    _check_writable(folder)
    _ext_save(db_path, {"dir": str(folder), "last_ok": "", "last_error": ""})
    return external_status(db_path)


def clear_external_dir(db_path) -> dict:
    external_wipe(db_path)
    return external_status(db_path)


def copy_external(db_path, source=None) -> dict:
    """Copia o banco para a pasta externa. `source` pode ser uma conexão aberta (cópia consistente)."""
    cfg = _ext_load(db_path)
    if not cfg.get("dir"):
        raise ValueError("Defina primeiro a pasta externa.")
    folder = Path(cfg["dir"])
    try:
        _check_writable(folder)
        dest = folder / f"{EXTERNAL_PREFIX}-{datetime.now():%Y%m%d-%H%M%S}.db"
        n = 1
        while dest.exists():
            n += 1
            dest = folder / f"{EXTERNAL_PREFIX}-{datetime.now():%Y%m%d-%H%M%S}-{n}.db"
        _snapshot(source if source is not None else db_path, dest)
        files = sorted(folder.glob(f"{EXTERNAL_PREFIX}-*.db"), key=lambda p: p.name, reverse=True)
        for old in files[EXTERNAL_KEEP:]:
            try:
                old.unlink()
            except OSError:
                pass
    except (OSError, ValueError, sqlite3.Error) as exc:
        cfg["last_error"] = str(exc)
        _ext_save(db_path, cfg)
        raise ValueError(f"A cópia externa falhou: {exc}") from None
    cfg["last_ok"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    cfg["last_error"] = ""
    _ext_save(db_path, cfg)
    return {"name": dest.name, "dir": str(folder)}


def sync_external(db_path, today: str) -> bool:
    """Cópia externa automática (no máximo uma por dia). Nunca levanta erro: o uso do app não pode depender disso."""
    cfg = _ext_load(db_path)
    if not cfg.get("dir") or str(cfg.get("last_ok", ""))[:10] == today or not _has_data(db_path):
        return False
    try:
        copy_external(db_path)
        return True
    except ValueError:
        return False


def external_status(db_path, today: str | None = None) -> dict:
    cfg = _ext_load(db_path)
    today = today or datetime.now().strftime("%Y-%m-%d")
    folder = cfg.get("dir") or ""
    exists = bool(folder) and Path(folder).is_dir()
    last = str(cfg.get("last_ok") or "")
    days = None
    if last:
        try:
            days = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(last[:10], "%Y-%m-%d")).days
        except ValueError:
            days = None
    has_data = _has_data(db_path) if Path(db_path).exists() else False
    return {"dir": folder, "configured": bool(folder), "folder_ok": exists, "last_ok": last, "days_since": days,
            "last_error": cfg.get("last_error") or "", "keep": EXTERNAL_KEEP,
            "stale": bool(folder) and (days is None or days > STALE_DAYS or not exists),
            "needs_setup": (not folder) and has_data, "suggestions": suggest_external_dirs()}


CONFIRM_WORD = "RESETAR"


def reset(conn, db_path, d: dict) -> dict:
    """Apaga TODOS os dados e volta às configurações de fábrica (para distribuir o app limpo).
    Sem `wipe_backups`, guarda antes uma cópia de segurança; com ele, apaga também todos os backups."""
    if str(d.get("confirm") or "").strip().upper() != CONFIRM_WORD:
        raise ValueError(f'Para confirmar, digite {CONFIRM_WORD}.')
    wipe = bool(d.get("wipe_backups"))
    conn.commit()  # a cópia consistente exige que não haja gravação pendente nesta conexão
    folder = backup_dir(db_path)
    safety = None
    if not wipe:
        safety = _unique(folder, "antes")
        _snapshot(conn, safety)
        _prune(folder, "antes", SAFETY_KEEP)
    conn.commit()
    conn.execute("PRAGMA defer_foreign_keys = ON")
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    for t in tables:
        conn.execute(f'DELETE FROM "{t}"')
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_sequence'").fetchone():
        conn.execute("DELETE FROM sqlite_sequence")
    db.init(conn)  # recoloca as configurações padrão
    conn.commit()
    conn.execute("VACUUM")  # remove de fato os dados antigos do arquivo
    removed = 0
    if wipe:
        for p in folder.glob("*.db"):
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    return {"safety": safety.name if safety else None, "backups_removed": removed}
