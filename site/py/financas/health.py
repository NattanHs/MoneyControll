"""Situação financeira do mês (saudável / ok / preocupante) e as maiores concentrações de gasto.

Regras simples e explicáveis, usadas na visão geral e no mês a mês:
Com o retrato financeiro (retrato.py) as metas e os textos se ajustam à situação da pessoa: no vermelho,
com dívida cara, renda irregular ou orçamento apertado. Sem retrato, o comportamento é o de sempre.
- receita prevista = entradas + a receber; gasto previsto = saídas + a pagar;
- sobra = receita - gasto; poupança = aportes em investimento no mês;
- taxa de poupança = (sobra + aportes) / receita.
"""
from __future__ import annotations

from . import limites

META_POUPANCA = limites.META_POUPANCA      # taxa de poupança considerada saudável
PESO_CATEGORIA = limites.PESO_CATEGORIA    # uma categoria acima disso do gasto total merece atenção
REDUCAO_SUGERIDA = limites.REDUCAO_SUGERIDA  # quanto reduzir na maior categoria para a sugestão


def _moeda(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def top_spending(by_category: list[dict], n: int = 3) -> list[dict]:
    total = sum(c["total"] for c in by_category)
    out = []
    for c in by_category[:n]:
        if c["total"] <= 0:
            continue
        out.append({"category": c["category"], "total": round(c["total"], 2),
                    "share": round(c["total"] / total, 4) if total else 0.0})
    return out


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def _extras_deficit(retrato: dict, receita: float) -> list[str]:
    """Frases que o retrato acrescenta quando o mês fecha no vermelho."""
    out = []
    div, renda, folga = retrato["divida"], retrato["renda"], retrato["folga"]
    if div["estado"] != "sem_divida_cara" and div["custo_mensal"] > 0:
        out.append(f"Há dívida com juros custando {_moeda(div['custo_mensal'])} por mês: ela é a prioridade, antes de cortar o resto.")
    if renda["estado"] != "estavel" and receita < renda["base"]:
        out.append(f"A receita prevista ({_moeda(receita)}) está abaixo da sua renda-base ({_moeda(renda['base'])}), "
                   "o pior mês do histórico. É para meses assim que serve o colchão: cubra primeiro o essencial.")
    if folga["meses_negativos"] >= 2:
        out.append(f"Isso se repete: {folga['meses_negativos']} dos últimos {retrato['meses']} meses fecharam no vermelho.")
    return out


def _tip_ajustado(retrato: dict, level: str, receita: float, sobra: float, aportes: float, tip: str) -> tuple[str, str | None, str | None]:
    """Devolve (nível, dica, título novo ou None) ajustados pelo retrato para um mês que não fechou no vermelho."""
    div, renda, folga = retrato["divida"], retrato["renda"], retrato["folga"]
    if div["estado"] != "sem_divida_cara":
        partes = [f"Dívida com juros custa {_moeda(div['custo_mensal'])} por mês: antes de investir, use a sobra para quitá-la, "
                  "porque ela custa mais do que qualquer aplicação rende."]
        if div["estado"] == "comprometida":
            partes.append(f"Parcelas e dívidas ocupam {_pct(div['comprometimento'])} da renda (o comum é até 30%).")
        return "ok", " ".join(partes), "Situação estável, mas com dívida cara"
    if level == "saudavel" and folga["estado"] == "vermelho":
        return "ok", ("O mês fechou bem, mas o histórico recente ainda pede cuidado: use o excedente para recompor "
                      "o saldo e a reserva antes de gastar mais."), "Mês positivo, mas o histórico pede cuidado"
    forte = renda["estado"] != "estavel" and renda["base"] > 0 and receita > renda["base"] * limites.MES_FORTE
    if forte:
        return level, (f"Mês acima da sua renda-base ({_moeda(renda['base'])}): separe o excedente (imposto, colchão e reserva) "
                       "antes de gastar. Meses fracos vão voltar."), None
    if level == "ok" and folga["estado"] == "apertado":
        falta = round(receita * limites.META_APERTADO - (sobra + aportes), 2)
        if falta > 0:
            return level, (f"Seu orçamento está apertado. Comece com uma meta menor: guardar {_pct(limites.META_APERTADO)} "
                           f"da receita (faltam {_moeda(falta)} por mês)."), None
        falta15 = round(receita * META_POUPANCA - (sobra + aportes), 2)
        return level, (f"Você já passa de {_pct(limites.META_APERTADO)}. O próximo passo é chegar a "
                       f"{_pct(META_POUPANCA)}: faltam {_moeda(max(falta15, 0))} por mês."), None
    return level, tip, None


def assess(summary: dict, by_category: list[dict], retrato: dict | None = None) -> dict:
    receita = round(summary.get("entradas", 0) + summary.get("a_receber", 0), 2)
    gasto = round(summary.get("saidas", 0) + summary.get("a_pagar", 0), 2)
    aportes = round(summary.get("aportes", 0), 2)
    sobra = round(receita - gasto, 2)
    taxa = round((sobra + aportes) / receita, 4) if receita > 0 else None
    top = top_spending(by_category, 1)
    maior = top[0] if top else None
    usar = bool(retrato and retrato.get("ok"))

    if receita <= 0 and gasto <= 0:
        return {"level": "sem_dados", "title": "Sem movimento neste mês",
                "message": "Registre entradas e saídas para ver a situação financeira.",
                "tip": None, "receita": receita, "gasto": gasto, "taxa_poupanca": taxa}

    if receita <= 0 or sobra < 0:
        level = "preocupante"
        title = "Atenção: as saídas passam das entradas"
        message = f"Previsto para o mês: gastos de {_moeda(gasto)} para uma receita de {_moeda(receita)}."
        if maior and maior["share"] >= PESO_CATEGORIA:
            tip = (f"Sua maior saída é em {maior['category']} ({maior['share']:.0%} do gasto). "
                   f"Reduzir {REDUCAO_SUGERIDA:.0%} ali libera {_moeda(maior['total'] * REDUCAO_SUGERIDA)} por mês.")
        else:
            tip = f"Revise as despesas do mês: faltam {_moeda(-sobra)} para fechar no azul."
        if usar:
            tip = " ".join([tip, *_extras_deficit(retrato, receita)])
    elif taxa is not None and taxa >= META_POUPANCA:
        level = "saudavel"
        title = "Situação saudável"
        message = f"Você deve guardar ou investir {taxa:.0%} da receita deste mês."
        tip = "Continue assim: destine o excedente a um objetivo ou à reserva de emergência."
    else:
        level = "ok"
        title = "Situação estável, com espaço para melhorar"
        message = (f"Sobra prevista de {_moeda(sobra)}"
                   + (f" e {taxa:.0%} da receita guardada." if taxa is not None else "."))
        falta = round(receita * META_POUPANCA - (sobra + aportes), 2)
        if falta > 0:
            tip = f"Para chegar a 15% de poupança, faltam {_moeda(falta)} por mês."
        else:
            tip = "Mantenha o padrão e acompanhe as maiores categorias de gasto."

    if usar and level != "preocupante":
        level, tip, novo_titulo = _tip_ajustado(retrato, level, receita, sobra, aportes, tip)
        title = novo_titulo or title

    out = {"level": level, "title": title, "message": message, "tip": tip,
           "receita": receita, "gasto": gasto, "taxa_poupanca": taxa}
    if usar:
        out["base_meses"] = retrato["meses"]
        out["confianca"] = retrato["confianca"]
    return out
