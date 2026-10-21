"""Leitor de extratos OFX (conta corrente e cartão de crédito), formatos SGML (1.x) e XML (2.x).

Só usa a biblioteca padrão. O arquivo é tratado como texto: nada é executado e nenhuma entidade externa
é resolvida (leitura por expressões regulares).
"""
from __future__ import annotations

import hashlib
import html
import re
from datetime import date

MAX_BYTES = 15_000_000
_TAG = re.compile(r"<([A-Za-z0-9_.]+)>([^<\r\n]*)")


def decode(raw: bytes) -> str:
    """Bancos brasileiros exportam em UTF-8 ou Windows-1252/Latin-1; tenta nessa ordem."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _fields(block: str) -> dict:
    out: dict = {}
    for tag, value in _TAG.findall(block):
        out.setdefault(tag.upper(), html.unescape(value).strip())
    return out


def parse_date(raw: str) -> str:
    """AAAAMMDD[HHMMSS[.mmm][fuso]] -> AAAA-MM-DD. O fuso é ignorado (vale a data do extrato)."""
    digits = re.match(r"\d{8}", (raw or "").strip())
    if not digits:
        raise ValueError(f"Data inválida no extrato: {raw!r}.")
    d = digits.group(0)
    try:
        return date(int(d[:4]), int(d[4:6]), int(d[6:8])).isoformat()
    except ValueError:
        raise ValueError(f"Data inválida no extrato: {raw!r}.") from None


def parse_amount(raw: str) -> float:
    """Aceita 1234.56, -1234.56, 1234,56 e 1.234,56."""
    s = (raw or "").strip().replace(" ", "")
    if not s:
        raise ValueError("Valor vazio no extrato.")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return round(float(s), 2)
    except ValueError:
        raise ValueError(f"Valor inválido no extrato: {raw!r}.") from None


def clean_text(*parts: str) -> str:
    seen, out = set(), []
    for p in parts:
        p = re.sub(r"\s+", " ", p or "").strip()
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return " · ".join(out)


def _statement(kind: str, block: str) -> dict:
    head = block.split("<BANKTRANLIST>", 1)[0] if "<BANKTRANLIST>" in block else block
    acct = _fields(head)
    acct_id = acct.get("ACCTID", "")
    bank_id = acct.get("BANKID", "")
    key = f"cartao:{acct_id}" if kind == "cartao" else f"conta:{bank_id}:{acct_id}"
    label = (f"Cartão final {acct_id[-4:]}" if kind == "cartao" else
             f"Conta {acct_id}" + (f" (banco {bank_id})" if bank_id else "")) if acct_id else (
        "Cartão de crédito" if kind == "cartao" else "Conta corrente")

    listing = block.split("<BANKTRANLIST>", 1)[1] if "<BANKTRANLIST>" in block else ""
    listing = listing.split("</BANKTRANLIST>", 1)[0]
    period = _fields(listing.split("<STMTTRN>", 1)[0])
    txns, seen = [], {}
    chunks = re.split(r"<STMTTRN>", listing, flags=re.I)[1:]
    for n, chunk in enumerate(chunks):
        chunk = re.split(r"</STMTTRN>", chunk, flags=re.I)[0]
        f = _fields(chunk)
        if "TRNAMT" not in f or "DTPOSTED" not in f:
            continue
        amount = parse_amount(f["TRNAMT"])
        day = parse_date(f["DTPOSTED"])
        memo = clean_text(f.get("NAME", ""), f.get("MEMO", ""))
        fitid = f.get("FITID", "")
        if not fitid:  # sem identificador: gera um estável a partir do conteúdo
            base = f"{day}|{amount}|{memo}"
            seen[base] = seen.get(base, 0) + 1
            fitid = "sx-" + hashlib.sha1(f"{base}|{seen[base]}".encode()).hexdigest()[:16]
        txns.append({"fitid": fitid[:80], "date": day, "amount": amount, "memo": memo or "(sem descrição)",
                     "trntype": f.get("TRNTYPE", "")[:20]})
    ids = [t["fitid"] for t in txns]
    if len(ids) != len(set(ids)):  # FITID repetido dentro do mesmo arquivo: diferencia pela ordem
        count: dict = {}
        for t in txns:
            count[t["fitid"]] = count.get(t["fitid"], 0) + 1
            if count[t["fitid"]] > 1:
                t["fitid"] = f"{t['fitid']}#{count[t['fitid']]}"[:80]
    ledger = _fields(block.split("<LEDGERBAL>", 1)[1].split("</LEDGERBAL>", 1)[0]) if "<LEDGERBAL>" in block else {}
    result = {"kind": kind, "account_key": key, "account_label": label, "txns": txns,
              "period_start": parse_date(period["DTSTART"]) if period.get("DTSTART") else None,
              "period_end": parse_date(period["DTEND"]) if period.get("DTEND") else None,
              "ledger_balance": None, "ledger_date": None}
    if ledger.get("BALAMT"):
        try:
            result["ledger_balance"] = parse_amount(ledger["BALAMT"])
            result["ledger_date"] = parse_date(ledger["DTASOF"]) if ledger.get("DTASOF") else None
        except ValueError:
            pass
    dates = [t["date"] for t in txns]
    if dates:
        result["period_start"] = result["period_start"] or min(dates)
        result["period_end"] = result["period_end"] or max(dates)
    return result


def parse(raw: bytes) -> dict:
    """Lê o arquivo e devolve o extrato (uma conta por arquivo)."""
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("Arquivo vazio ou grande demais para um extrato OFX.")
    text = re.sub(r"</?[A-Za-z0-9_.]+>", lambda m: m.group(0).upper(), decode(raw))  # tags em maiúsculas
    if "<OFX" not in text.upper():
        raise ValueError("Este arquivo não parece ser um extrato OFX. Baixe o extrato no formato OFX no internet banking.")
    if re.search(r"<INVSTMTMSGSRSV1>", text, re.I) and not re.search(r"<(STMTRS|CCSTMTRS)>", text, re.I):
        raise ValueError("Extratos de corretora/investimentos não são suportados. Use o extrato da conta ou do cartão.")
    statements = [_statement("cartao", b) for b in re.split(r"<CCSTMTRS>", text, flags=re.I)[1:]]
    statements += [_statement("conta", b) for b in re.split(r"<STMTRS>", text, flags=re.I)[1:]]
    if not statements:
        raise ValueError("Nenhum extrato encontrado no arquivo.")
    if len(statements) > 1:
        raise ValueError("O arquivo tem mais de uma conta. Exporte um extrato por vez.")
    st = statements[0]
    if not st["txns"]:
        raise ValueError("O extrato não tem nenhuma transação.")
    return st
