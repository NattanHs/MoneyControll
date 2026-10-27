"""Relatórios: comparativo mensal, categorias, contas e maiores gastos de um período."""
from __future__ import annotations

import csv
import io

from . import accounts, cards, finance
from . import validate as v
from .common import ym_add, ym_diff

MAX_MONTHS = 36
TOP_N = 15


def _months(start: str, end: str) -> list[str]:
    out, ym = [], start
    while ym <= end:
        out.append(ym)
        ym = ym_add(ym, 1)
    return out


def _month_totals(items: list[dict]) -> dict:
    """Totais do mês pelo que foi lançado (recebido ou não, pago ou não): visão de competência."""
    entradas = sum(i["amount"] for i in items if i["kind"] == "entrada")
    saidas = sum(i["amount"] for i in items if i["kind"] == "saida" and not i.get("transfer"))
    aportes = sum(i["amount"] for i in items if i["kind"] == "invest")
    return {"entradas": round(entradas, 2), "saidas": round(saidas, 2), "aportes": round(aportes, 2),
            "resultado": round(entradas - saidas - aportes, 2)}


def _sum(rows: list[dict], key: str) -> float:
    return round(sum(r[key] for r in rows), 2)


def _period(data: dict, months: list[str], today: str, account: int | None) -> dict:
    per_month, cat_total, cat_by_month, spend = [], {}, {}, []
    balance = {m["ym"]: m for m in finance.run(data, months[-1], today, account)}
    for ym in months:
        items = finance.items_for_month(data, ym, today, account)
        row = {"ym": ym, **_month_totals(items), "saldo_final": balance[ym]["saldo_final"] if ym in balance else None}
        per_month.append(row)
        for c in finance.spending_by_category(items):
            cat_total[c["category"]] = cat_total.get(c["category"], 0.0) + c["total"]
            cat_by_month.setdefault(c["category"], {})[ym] = c["total"]
        for i in items:
            if i["kind"] != "saida" or i.get("transfer") or i.get("invoice_payment"):
                continue
            if i["source"] == "fatura":
                card = next((c for c in data["cards"] if c["id"] == i["ref_id"]), None)
                for ln in (cards.statement_lines(data, card, ym) if card else []):
                    spend.append({"date": ln["date"], "description": ln["description"], "category": ln["category"] or "Sem categoria",
                                  "amount": ln["amount"], "account_id": i.get("account_id")})
            else:
                spend.append({"date": i["date"], "description": i["description"], "category": i["category"] or "Sem categoria",
                              "amount": i["amount"], "account_id": i.get("account_id")})
    return {"per_month": per_month, "cat_total": cat_total, "cat_by_month": cat_by_month, "spend": spend}


def report(conn, start: str, end: str, today: str, account=None) -> dict:
    start = v.month({"s": start}, "s", "o mês inicial")
    end = v.month({"e": end}, "e", "o mês final")
    if start > end:
        raise ValueError("O mês inicial é depois do final.")
    n = ym_diff(start, end) + 1
    if n > MAX_MONTHS:
        raise ValueError(f"Escolha no máximo {MAX_MONTHS} meses por relatório.")
    acc = accounts.check_account(conn, account) if account not in (None, "", 0, "0") else None
    data = finance.load_all(conn)
    months = _months(start, end)
    cur = _period(data, months, today, acc)
    pm = cur["per_month"]
    totals = {k: _sum(pm, k) for k in ("entradas", "saidas", "aportes", "resultado")}
    avg = {k: round(totals[k] / n, 2) for k in totals}
    all_spend = sum(cur["cat_total"].values()) or 0.0
    cats = []
    for name, total in sorted(cur["cat_total"].items(), key=lambda kv: -kv[1]):
        cats.append({"category": name, "total": round(total, 2), "pct": round(total / all_spend * 100, 1) if all_spend else 0.0,
                     "avg": round(total / n, 2), "by_month": [round(cur["cat_by_month"].get(name, {}).get(ym, 0.0), 2) for ym in months]})
    # período anterior de mesmo tamanho, para comparar
    prev = None
    pstart, pend = ym_add(start, -n), ym_add(start, -1)
    earliest = finance.earliest_month(data)
    if earliest and pend >= earliest:
        pcur = _period(data, _months(pstart, pend), today, acc)
        ptot = {k: _sum(pcur["per_month"], k) for k in ("entradas", "saidas", "aportes", "resultado")}
        pcats = {k: round(t, 2) for k, t in pcur["cat_total"].items()}
        prev = {"start": pstart, "end": pend, "totals": ptot}
        for c in cats:
            c["prev_total"] = pcats.get(c["category"], 0.0)
            c["delta"] = round(c["total"] - c["prev_total"], 2)
    by_account = []
    if acc is None and len(data["accounts"]) > 1:
        for a in data["accounts"]:
            part = _period(data, months, today, a["id"])["per_month"]
            by_account.append({"id": a["id"], "name": a["name"], **{k: _sum(part, k) for k in ("entradas", "saidas", "aportes")},
                               "saldo_final": part[-1]["saldo_final"] if part else None})
    names = {a["id"]: a["name"] for a in data["accounts"]}
    top = sorted(cur["spend"], key=lambda s: -s["amount"])[:TOP_N]
    for t in top:
        t["account"] = names.get(t.pop("account_id"), "")
    return {"start": start, "end": end, "months_count": n, "account": acc, "account_name": names.get(acc, "") if acc else "",
            "months": pm, "totals": totals, "average": avg, "categories": cats, "top": top, "previous": prev,
            "accounts": by_account, "generated": today}


# ---------------------------------------------------------------- exportação

def _sheets(rep: dict) -> list[tuple[str, list[list]]]:
    """Abas do relatório como linhas de células; (valor, estilo) onde o estilo é h, money, pct ou date."""
    h = lambda *cols: [(c, "h") for c in cols]  # noqa: E731
    mny = lambda x: (x, "money")  # noqa: E731
    title = f"MoneyControl · {rep['start']} a {rep['end']}" + (f" · {rep['account_name']}" if rep["account_name"] else "")
    resumo = [[(title, "h")], [], h("Mês", "Entradas", "Saídas", "Aportes", "Resultado", "Saldo final")]
    for m in rep["months"]:
        resumo.append([m["ym"], mny(m["entradas"]), mny(m["saidas"]), mny(m["aportes"]), mny(m["resultado"]),
                       mny(m["saldo_final"]) if m["saldo_final"] is not None else ""])
    t = rep["totals"]
    resumo.append([("Total", "h"), mny(t["entradas"]), mny(t["saidas"]), mny(t["aportes"]), mny(t["resultado"]), ""])
    a = rep["average"]
    resumo.append([("Média mensal", "h"), mny(a["entradas"]), mny(a["saidas"]), mny(a["aportes"]), mny(a["resultado"]), ""])
    cat = [h("Categoria", *[m["ym"] for m in rep["months"]], "Total", "% das saídas", "Média mensal")]
    for c in rep["categories"]:
        cat.append([c["category"], *[mny(x) for x in c["by_month"]], mny(c["total"]), (c["pct"] / 100, "pct"), mny(c["avg"])])
    sheets = [("Resumo mensal", resumo), ("Categorias", cat)]
    if rep["accounts"]:
        sheets.append(("Por conta", [h("Conta", "Entradas", "Saídas", "Aportes", "Saldo final")]
                       + [[x["name"], mny(x["entradas"]), mny(x["saidas"]), mny(x["aportes"]), mny(x["saldo_final"]) if x["saldo_final"] is not None else ""] for x in rep["accounts"]]))
    sheets.append(("Maiores gastos", [h("Data", "Descrição", "Categoria", "Conta", "Valor")]
                   + [[(s["date"], "date"), s["description"], s["category"], s["account"], mny(s["amount"])] for s in rep["top"]]))
    return sheets


def to_xlsx(rep: dict) -> bytes:
    from . import xlsx
    return xlsx.write(_sheets(rep))


def to_csv(rep: dict) -> bytes:
    """CSV para Excel em português: separador ';', vírgula decimal e BOM para os acentos."""
    out = io.StringIO()
    w = csv.writer(out, delimiter=";", lineterminator="\r\n")
    for name, rows in _sheets(rep):
        w.writerow([name.upper()])
        for row in rows:
            cells = []
            for c in row:
                val, kind = c if isinstance(c, tuple) else (c, "")
                if kind == "money" and val != "":
                    val = f"{val:.2f}".replace(".", ",")
                elif kind == "pct":
                    val = f"{val * 100:.1f}".replace(".", ",") + "%"
                cells.append(val)
            w.writerow(cells)
        w.writerow([])
    return ("﻿" + out.getvalue()).encode("utf-8")
