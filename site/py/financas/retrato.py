"""Retrato financeiro: como está o dinheiro da pessoa, lido do histórico (nunca aparece como rótulo na tela).

Três eixos, todos calculados dos meses fechados (até 12, mínimo 3):
- renda: estavel, variavel ou irregular, e a renda-base (o pior mês, não a média);
- folga: vermelho, apertado, equilibrado ou folgado;
- divida: sem_divida_cara, divida_cara ou comprometida.

É uma função pura: lê os dados e devolve um dicionário. Os outros módulos usam o retrato para ajustar
metas e textos; se ele não existir (poucos meses) ou falhar, tudo segue como antes.
"""
from __future__ import annotations

import math
import statistics

from . import cards, finance, limites, perfil


def historico(data: dict, today: str) -> list[dict]:
    """Meses fechados (antes do mês atual) com movimento, dos mais recentes, até HISTORICO_MESES.

    A análise contextual (excesso em vestuário, troca de eletrônico) usa TODO o histórico,
    porque uma troca de celular pode ter acontecido antes da janela de 12 meses."""
    fim = finance.ym_add(today[:7], -1)
    base = []
    for m in finance.run(data, fim, today):  # ascendente
        items = finance.items_for_month(data, m["ym"], today)
        aporte_caixa = sum(i["amount"] for i in items if i["kind"] == "invest" and not i.get("no_cash"))
        base.append({"ym": m["ym"], "renda": m["entradas"], "faixas": perfil.faixas(items),
                     "compras": perfil.compras_eletronicas(items), "aporte_caixa": aporte_caixa,
                     "saldo_final": m["saldo_final"], "essenciais": perfil.essenciais_por_categoria(items),
                     "nao_essenciais": perfil.nao_essenciais_por_categoria(items),
                     "parcelas": _parcelas(data, items, m["ym"])})
    perfil.resolver(base)

    out = []
    for m in reversed(base):
        gasto = round(sum(v for k, v in m["faixas"].items() if k != "investimento"), 2)  # investir não é gasto
        aporte = round(m["aporte_caixa"] + m["faixas"].get("investimento", 0.0), 2)
        if m["renda"] <= 0 and gasto <= 0:
            continue
        out.append({"ym": m["ym"], "renda": m["renda"], "gasto": gasto, "faixas": m["faixas"],
                    "aporte": aporte, "sobra": m["renda"] - gasto - aporte, "saldo_final": m["saldo_final"],
                    "essencial": round(m["faixas"]["essencial"] + m["faixas"]["divida"], 2),
                    "essenciais": m["essenciais"], "nao_essenciais": m["nao_essenciais"],
                    "parcelas": m["parcelas"]})
        if len(out) >= limites.HISTORICO_MESES:
            break
    return out


def _parcelas(data: dict, items: list[dict], ym: str) -> float:
    """Parcelas do mês (compras parceladas, com ou sem cartão), sem contar o que já é dívida cara
    (juros, empréstimos), que o eixo da dívida mede à parte."""
    total = 0.0
    for i in items:
        if i["source"] == "parcela" and i["kind"] == "saida" and perfil.classificar(i["category"], i["description"]) != "divida":
            total += i["amount"]
    for card in data.get("cards", []):
        for line in cards.statement_lines(data, card, ym):
            if line["source"] == "parcela" and perfil.classificar(line["category"], line["description"]) != "divida":
                total += line["amount"]
    return round(total, 2)


def _renda(hist: list[dict]) -> dict:
    rendas = [m["renda"] for m in hist]
    media = statistics.mean(rendas)
    cv = (statistics.pstdev(rendas) / media) if media > 0 else None
    if cv is None or cv > limites.RENDA_CV_IRREGULAR:
        estado = "irregular"
    elif cv >= limites.RENDA_CV_ESTAVEL:
        estado = "variavel"
    else:
        estado = "estavel"
    return {"estado": estado, "cv": None if cv is None else round(cv, 4), "media": round(media, 2),
            "base": round(min(rendas), 2)}


def _folga(hist: list[dict]) -> dict:
    n = len(hist)
    renda_total = sum(m["renda"] for m in hist)
    resultado = sum(m["renda"] - m["gasto"] for m in hist)  # antes de investir
    taxa = (resultado / renda_total) if renda_total > 0 else (-1.0 if resultado < 0 else 0.0)
    negativos = sum(1 for m in hist if m["renda"] - m["gasto"] < 0)
    minimo = max(limites.MESES_NEGATIVOS_MIN, math.ceil(limites.MESES_NEGATIVOS_FRACAO * n))
    conta_negativa = hist[0]["saldo_final"] < 0
    if taxa < 0 or negativos >= minimo or conta_negativa:
        estado = "vermelho"
    elif taxa < limites.FOLGA_APERTADO:
        estado = "apertado"
    elif taxa < limites.FOLGA_FOLGADO:
        estado = "equilibrado"
    else:
        estado = "folgado"
    return {"estado": estado, "taxa": round(taxa, 4), "meses_negativos": negativos,
            "conta_negativa": conta_negativa}


def _divida(hist: list[dict]) -> dict:
    recentes = hist[:limites.DIVIDA_JANELA]
    com_divida = sum(1 for m in recentes if m["faixas"].get("divida", 0) > 0)
    cara = com_divida >= limites.DIVIDA_RECORRENTE_MIN
    medias = []
    for m in recentes:
        peso = m["parcelas"] + m["faixas"].get("divida", 0.0)
        medias.append(peso / m["renda"] if m["renda"] > 0 else (1.0 if peso > 0 else 0.0))
    comprometimento = statistics.mean(medias) if medias else 0.0
    if comprometimento >= limites.COMPROMETIMENTO_CRITICO or (cara and comprometimento >= limites.COMPROMETIMENTO_ALTO):
        estado = "comprometida"
    elif cara:
        estado = "divida_cara"
    else:
        estado = "sem_divida_cara"
    nivel = ("critico" if comprometimento >= limites.COMPROMETIMENTO_CRITICO
             else "alto" if comprometimento >= limites.COMPROMETIMENTO_ALTO else "normal")
    custo = statistics.mean(m["faixas"].get("divida", 0.0) for m in recentes) if recentes else 0.0
    return {"estado": estado, "custo_mensal": round(custo, 2), "comprometimento": round(comprometimento, 4),
            "nivel": nivel}


def confianca(n: int) -> float:
    """0 a 1: quanto o retrato merece confiança, conforme os meses de histórico."""
    if n < limites.MIN_MESES:
        return 0.0
    return min(1.0, (n - (limites.MIN_MESES - 1)) / (limites.HISTORICO_MESES - (limites.MIN_MESES - 1)))


def de_historico(hist: list[dict]) -> dict:
    n = len(hist)
    if n < limites.MIN_MESES:
        return {"ok": False, "meses": n, "confianca": 0.0,
                "motivo": f"São necessários pelo menos {limites.MIN_MESES} meses de movimentação."}
    return {"ok": True, "meses": n, "confianca": round(confianca(n), 4),
            "renda": _renda(hist), "folga": _folga(hist), "divida": _divida(hist)}


def construir(data: dict, today: str) -> dict:
    return de_historico(historico(data, today))


def seguro(data: dict, today: str) -> dict | None:
    """Como construir(), mas nunca derruba quem chama: se algo falhar, devolve None e a Saúde
    volta ao comportamento de sempre."""
    try:
        return construir(data, today)
    except Exception:  # noqa: BLE001
        return None
