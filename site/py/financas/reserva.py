"""Reserva de emergência sugerida.

Base: 6 meses de custo de vida (média das saídas dos meses fechados).

Ajuste pelo perfil, feito a partir do histórico. O perfil é uma leitura interna e NUNCA aparece no app:
a tela mostra só o número de meses, os valores e a precisão (quantos meses foram lançados).

Nota de perfil (0 a 1, quanto maior, mais organizado está o dinheiro), com pesos:
- taxa de sobra média (sobra + aportes sobre a renda), limitada entre -20% e +30%     35%
- qualidade dos gastos (faixas do módulo perfil: essencial, lazer, delivery, supérfluo, dívida)  30%
- meses com saldo negativo no fluxo                                                   15%
- meses com conta no vermelho                                                         10%
- regularidade dos gastos (variação entre meses)                                      10%

Precisão: com menos de 3 meses lançados, não há ajuste (vale a base). A partir daí, o peso do
ajuste cresce até a precisão total em 12 meses. Ou seja, o ajuste é gradual, não um salto.

Quem está organizado precisa de menos folga (4 meses); no meio, a base (6); com muita irregularidade,
dívida ou meses no vermelho, mais folga (9). Sempre entre 3 e 12 meses.
"""
from __future__ import annotations

import math
import statistics

from . import db, finance, goals, perfil, retrato

BASE_MESES = 6
MIN_MESES, MAX_MESES = 3, 12
HISTORICO = 12
MIN_HISTORICO_AJUSTE = 3
RESERVA_NOME = "Reserva de emergência"
FRACAO_FOLGA = 0.5  # parte da sobra média que pode ir para a reserva sem apertar o resto


def _historico(data: dict, today: str) -> list[dict]:
    """Meses fechados com movimento (ver retrato.historico, que guarda a leitura do histórico)."""
    return retrato.historico(data, today)


def _nota_perfil(hist: list[dict]) -> float:
    rendas = [m["renda"] for m in hist if m["renda"] > 0]
    taxa = (sum(m["sobra"] + m["aporte"] for m in hist if m["renda"] > 0) / sum(rendas)) if rendas else 0.0
    taxa_norm = (max(-0.20, min(0.30, taxa)) + 0.20) / 0.50

    total = sum(m["gasto"] for m in hist)
    penalidade = sum(perfil.PESO[k] * sum(m["faixas"][k] for m in hist)
                     for k in perfil.PESO if k != "investimento") / total if total else 0.0
    qualidade = 1 - min(penalidade, 1.0)

    frac_neg = sum(1 for m in hist if m["sobra"] < 0) / len(hist)
    frac_vermelho = sum(1 for m in hist if m["saldo_final"] < 0) / len(hist)
    gastos = [m["gasto"] for m in hist]
    media = statistics.mean(gastos)
    cv = (statistics.pstdev(gastos) / media) if media > 0 else 0.0

    nota = (0.35 * taxa_norm + 0.30 * qualidade + 0.15 * (1 - frac_neg)
            + 0.10 * (1 - frac_vermelho) + 0.10 * (1 - min(cv, 1.0)))
    return max(0.0, min(1.0, nota))


def _precisao(n: int) -> float:
    """0 a 1: fração de confiança no ajuste pelo perfil, conforme os meses lançados."""
    if n < MIN_HISTORICO_AJUSTE:
        return 0.0
    return min(1.0, (n - (MIN_HISTORICO_AJUSTE - 1)) / (HISTORICO - (MIN_HISTORICO_AJUSTE - 1)))


def _meses(hist: list[dict]) -> tuple[int, float]:
    precisao = _precisao(len(hist))
    if precisao == 0:
        return BASE_MESES, 0.0
    nota = _nota_perfil(hist)
    if nota >= 0.70:
        ajustado = 4
    elif nota >= 0.40:
        ajustado = BASE_MESES
    else:
        ajustado = 9
    meses = BASE_MESES + precisao * (ajustado - BASE_MESES)
    return max(MIN_MESES, min(MAX_MESES, round(meses))), precisao


def _composicao(hist: list[dict]) -> list[dict]:
    """De onde vem o custo essencial médio: categoria, valor mensal e participação."""
    soma: dict[str, float] = {}
    for m in hist:
        for cat, valor in m["essenciais"].items():
            soma[cat] = soma.get(cat, 0.0) + valor
    total = sum(soma.values())
    if total <= 0:
        return []
    out = [{"categoria": c, "mensal": round(v / len(hist), 2), "percentual": round(v / total * 100)}
           for c, v in soma.items()]
    return sorted(out, key=lambda x: -x["mensal"])


def suggest(conn, today: str) -> dict:
    data = finance.load_all(conn)
    hist = _historico(data, today)
    if not hist:
        return {"ok": False, "message": "Registre alguns meses de entradas e saídas para calcular a sugestão.",
                "custo_mensal": 0, "renda_mensal": 0, "meses_base": BASE_MESES, "meses_sugeridos": None,
                "alvo_situacao": 0, "alvo_renda": 0, "meses_historico": 0, "precisao": 0,
                "sobra_media": 0, "composicao": []}
    custo = round(statistics.mean(m["essencial"] for m in hist), 2)  # a reserva cobre o essencial
    renda = round(statistics.mean(m["renda"] for m in hist), 2)
    meses, precisao = _meses(hist)
    return {
        "ok": True,
        "message": f"Calculado com base nos últimos {len(hist)} meses de movimentação.",
        "custo_mensal": custo,
        "renda_mensal": renda,
        "meses_base": BASE_MESES,
        "meses_sugeridos": meses,
        "alvo_situacao": round(custo * meses, 2),
        "alvo_renda": round(renda * BASE_MESES, 2),
        "meses_historico": len(hist),
        "precisao": round(precisao * 100),
        "sobra_media": round(statistics.mean(m["sobra"] for m in hist), 2),
        "composicao": _composicao(hist),
    }


AVISO_IMPORTANCIA = ("A reserva de emergência é tão importante quanto pagar os gastos essenciais: "
                     "é ela que evita uma dívida quando um imprevisto acontece.")


def status(conn, today: str) -> dict | None:
    """Situação da reserva: cobertura em meses de gasto essencial, falta, aporte sugerido e aviso."""
    data = finance.load_all(conn)
    r = next((g for g in goals.list_goals(conn, today) if g["reserve"]), None)
    if r is None:
        return None
    s = suggest(conn, today)
    if not s["ok"]:
        return {"existe": True, "pronta": False, "message": s["message"]}
    custo = s["custo_mensal"]
    saved, target = r["saved"], r["target"]
    falta = round(max(target - saved, 0.0), 2)
    cobertura = round(saved / custo, 1) if custo > 0 else 0.0
    if cobertura < 3:
        nivel, titulo = "critico", "Atenção: sua reserva está muito baixa"
    elif cobertura < s["meses_sugeridos"]:
        nivel, titulo = "atencao", "Reserva em construção"
    else:
        nivel, titulo = "ok", "Reserva na meta"
    # aporte precisa caber na folga real (metade da sobra média, o resto fica para imprevistos)
    folga = round(max(s["sobra_media"], 0.0) * FRACAO_FOLGA, 2)
    ideal12 = round(falta / 12, 2)
    if falta == 0:
        aporte, prazo, cabe = 0.0, 0, True
    elif folga <= 0:
        aporte, prazo, cabe = 0.0, None, False
    elif ideal12 <= folga:
        aporte, prazo, cabe = ideal12, 12, True
    else:
        aporte, prazo, cabe = folga, min(120, math.ceil(falta / folga)), False
    return {
        "existe": True, "pronta": True, "nivel": nivel, "titulo": titulo,
        "aviso": AVISO_IMPORTANCIA,
        "saved": saved, "target": target, "falta": falta, "pct": r["pct"],
        "custo_essencial": custo, "cobertura_meses": cobertura, "meses_meta": s["meses_sugeridos"],
        "aporte_sugerido": aporte, "prazo_meses": prazo, "sobra_media": round(max(s["sobra_media"], 0.0), 2),
        "folga_mensal": folga,
        "aporte_cabe": cabe, "composicao": s["composicao"],
        "texto_aporte": _texto_aporte(falta, aporte, prazo, folga),
    }


def _texto_aporte(falta, aporte, prazo, folga) -> str:
    if falta == 0:
        return "Meta atingida. Continue mantendo a reserva."
    if folga <= 0:
        return "Hoje suas saídas não deixam folga para aportar. Reduza primeiro os gastos não essenciais (veja o consultor)."
    if prazo == 12:
        return f"Para completar em 12 meses, aporte {fmt_moeda(aporte)} por mês."
    return f"Dentro da sua folga, aporte {fmt_moeda(aporte)} por mês: a meta fica pronta em cerca de {prazo} meses."


def fmt_moeda(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def apply(conn, today: str) -> int:
    """Grava a sugestão atual como objetivo de reserva (um só). Ignora qualquer valor enviado pelo cliente."""
    s = suggest(conn, today)
    if not s["ok"]:
        raise ValueError(s["message"])
    alvo = s["alvo_situacao"]
    row = db.rows(conn, "SELECT id FROM goals WHERE reserve = 1")
    if row:
        conn.execute("UPDATE goals SET target = ?, name = ? WHERE id = ?", (alvo, RESERVA_NOME, row[0]["id"]))
        return row[0]["id"]
    return goals.save_goal(conn, {"name": RESERVA_NOME, "target": alvo, "saved": 0, "monthly": 0, "reserve": 1})
