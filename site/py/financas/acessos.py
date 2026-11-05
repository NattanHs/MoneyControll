"""Controle de acesso por usuário e senha (versão de celular do escritório).

O cadastro é feito por quem distribui o app, com o `ferramentas/cadastrar-cliente.py`. Aqui ficam apenas as
senhas **embaralhadas** (hash PBKDF2), nunca a senha em si. A conferência acontece dentro do próprio aparelho.

Observação honesta: como todo o código roda no navegador e o repositório é público, isto trava o uso casual
(compartilhar login), mas não é à prova de alguém com conhecimento técnico. Os dados do cliente continuam no
aparelho dele; o login não os guarda na nuvem.

Com `CONTAS` vazio, o app fica **aberto** (sem pedir login) — útil para testar.
"""
from __future__ import annotations

import hashlib
import hmac

ITERS = 200_000

# As contas ficam em acessos_contas.py (só dados), editado pelo ferramentas/cadastrar-cliente.py.
# Formato por usuário: "usuario": {"nome": "Fulano", "salt": "<hex>", "hash": "<hex>", "iters": 200000, "ate": "AAAA-MM-DD" ou ""}
try:
    from .acessos_contas import CONTAS  # type: ignore
except Exception:  # noqa: BLE001  (sem o arquivo de contas, o app fica aberto)
    CONTAS: dict[str, dict] = {}


def _norm(user) -> str:
    return str(user or "").strip().lower()


def _pbkdf2_sha256(senha: bytes, salt: bytes, iters: int) -> bytes:
    """PBKDF2-HMAC-SHA256 (32 bytes). Usa o do hashlib quando existe; no Pyodide (navegador) o hashlib vem
    sem `pbkdf2_hmac` (sem OpenSSL), então cai numa versão em Python puro que dá o MESMO resultado."""
    nativo = getattr(hashlib, "pbkdf2_hmac", None)
    if nativo is not None:
        try:
            return nativo("sha256", senha, salt, iters)
        except Exception:  # noqa: BLE001  (algo errado no nativo: usa o de Python puro)
            pass
    base = hmac.new(senha, digestmod=hashlib.sha256)
    h = base.copy()
    h.update(salt + (1).to_bytes(4, "big"))
    u = h.digest()
    acc = int.from_bytes(u, "big")
    for _ in range(iters - 1):
        h = base.copy()
        h.update(u)
        u = h.digest()
        acc ^= int.from_bytes(u, "big")
    return acc.to_bytes(32, "big")


def _hash(senha, salt_hex: str, iters: int) -> str:
    return _pbkdf2_sha256(str(senha or "").encode("utf-8"), bytes.fromhex(salt_hex), iters).hex()


def required() -> bool:
    return len(CONTAS) > 0


def _expirada(conta: dict, today: str) -> bool:
    ate = str(conta.get("ate") or "")
    return bool(ate) and ate < today


def verificar(user, senha, today: str) -> dict:
    """Confere usuário + senha. Levanta ValueError se não bater ou se o acesso venceu."""
    conta = CONTAS.get(_norm(user))
    if not conta:
        raise ValueError("Usuário ou senha incorretos.")
    calc = _hash(senha, conta["salt"], int(conta.get("iters", ITERS)))
    if not hmac.compare_digest(calc, str(conta["hash"])):
        raise ValueError("Usuário ou senha incorretos.")
    if _expirada(conta, today):
        raise ValueError("Seu acesso venceu. Fale com o suporte para renovar.")
    return {"ok": True, "usuario": _norm(user), "nome": conta.get("nome") or _norm(user)}


def valido(user, today: str) -> dict:
    """Usado a cada abertura: diz se o usuário guardado ainda existe e não venceu (sem pedir a senha de novo)."""
    conta = CONTAS.get(_norm(user))
    if not conta or _expirada(conta, today):
        return {"valido": False}
    return {"valido": True, "nome": conta.get("nome") or _norm(user)}
