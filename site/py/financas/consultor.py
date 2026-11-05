"""Consultor financeiro: lê a saúde financeira inteira e sugere o que fazer, em ordem de prioridade.

É um motor de regras local (não usa IA externa): seus dados não saem do aparelho.
Regras de ouro:
- nunca sugerir corte em gasto essencial;
- todo número vem do histórico real da pessoa (médias dos meses fechados);
- cortes são de gastos não essenciais, e a sugestão diz quanto libera por mês;
- aportes respeitam a folga real (ver reserva.status).

Limiares de gasto não essencial (participação máxima da renda antes de sugerir corte):
lazer 10%, alimentação fora de casa 5%, supérfluo 3%.
"""
from __future__ import annotations

from . import finance, reserva

LIMITE = {"lazer": 0.10, "alimentacao_fora": 0.05, "superfluo": 0.03}
FRACAO_CORTE = 0.30   # quanto de cada gasto não essencial é razoável reduzir
META_POUPANCA = 0.15


def _moeda(v: float) -> str:
    return reserva.fmt_moeda(v)


def analisar(conn, today: str) -> dict:
    data = finance.load_all(conn)
    hist = reserva._historico(data, today)
    if not hist:
        return {"ok": False, "message": "Registre alguns meses de movimentação para receber recomendações.",
                "recomendacoes": []}
    n = len(hist)
    renda = sum(m["renda"] for m in hist) / n
    sobra = sum(m["sobra"] for m in hist) / n
    aporte = sum(m["aporte"] for m in hist) / n
    divida = sum(m["faixas"]["divida"] for m in hist) / n
    essencial = sum(m["essencial"] for m in hist) / n

    medias: dict[str, dict] = {}
    for m in hist:
        for cat, info in m["nao_essenciais"].items():
            cur = medias.setdefault(cat, {"faixa": info["faixa"], "total": 0.0})
            cur["total"] += info["total"]
            if info["faixa"] == "superfluo":
                cur["faixa"] = "superfluo"
    for cat in medias:
        medias[cat]["total"] = round(medias[cat]["total"] / n, 2)
    ordenadas = sorted(medias.items(), key=lambda kv: -kv[1]["total"])
    maior_corte = next((c for c, i in ordenadas if i["total"] > 0), None)

    recs: list[dict] = []
    if sobra < 0:
        recs.append({"prioridade": 0, "tipo": "orcamento", "titulo": "Ajuste o orçamento primeiro",
                     "texto": (f"Em média, suas saídas passam da renda em {_moeda(-sobra)} por mês. "
                               + (f"Comece pelo gasto não essencial mais alto: {maior_corte}."
                                  if maior_corte else "Revise os gastos antes de qualquer aporte.")),
                     "impacto": round(-sobra, 2)})
    if divida > 0:
        recs.append({"prioridade": 1, "tipo": "divida", "titulo": "Quite ou renegocie as dívidas",
                     "texto": (f"Você paga em média {_moeda(divida)} por mês em juros, rotativo ou empréstimos. "
                               "Esse é o gasto que mais corrói o orçamento: vale priorizar sua quitação."),
                     "impacto": round(divida, 2)})

    st = reserva.status(conn, today)
    if st and st.get("pronta") and st["nivel"] != "ok":
        recs.append({"prioridade": 2, "tipo": "reserva", "titulo": "Reforce a reserva de emergência",
                     "texto": (f"Ela cobre {st['cobertura_meses']} meses de gastos essenciais. {st['texto_aporte']}"),
                     "impacto": st["aporte_sugerido"]})

    for cat, info in ordenadas:
        limite = LIMITE[info["faixa"]] * renda
        if renda <= 0 or info["total"] <= limite or info["total"] <= 0:
            continue
        economia = round(info["total"] * FRACAO_CORTE, 2)
        dica = {"alimentacao_fora": " Delivery e fast food costumam ter custo acima do necessário.",
                "lazer": " A ideia é moderar, não cortar tudo.",
                "superfluo": " Compras por impulso costumam ser o primeiro ajuste que não pesa no dia a dia."}[info["faixa"]]
        recs.append({"prioridade": 3, "tipo": "corte", "titulo": f"Gaste menos com {cat}",
                     "texto": (f"Em média, {_moeda(info['total'])} por mês "
                               f"({round(info['total'] / renda * 100)}% da renda).{dica} "
                               f"Reduzir 30% libera {_moeda(economia)} por mês."),
                     "impacto": economia, "categoria": cat})

    taxa = (sobra + aporte) / renda if renda > 0 else 0.0
    if sobra >= 0 and renda > 0 and taxa < META_POUPANCA:
        falta = round(renda * META_POUPANCA - (sobra + aporte), 2)
        recs.append({"prioridade": 4, "tipo": "poupanca", "titulo": "Chegue a 15% de poupança",
                     "texto": (f"Hoje você guarda ou investe cerca de {round(max(taxa, 0) * 100)}% da renda. "
                               f"Para chegar a 15%, faltam {_moeda(falta)} por mês."),
                     "impacto": falta})

    if not recs:
        recs.append({"prioridade": 9, "tipo": "ok", "titulo": "Continue assim",
                     "texto": "Suas finanças estão equilibradas. Mantenha a reserva em dia e revise os gastos a cada mês.",
                     "impacto": 0})

    recs.sort(key=lambda r: (r["prioridade"], -r["impacto"]))
    nivel = ("preocupante" if any(r["prioridade"] == 0 for r in recs)
             else "atencao" if any(r["prioridade"] in (1, 2) for r in recs) else "saudavel")
    return {"ok": True, "nivel": nivel, "meses": n,
            "diagnostico": {"renda": round(renda, 2), "gasto_essencial": round(essencial, 2),
                            "sobra": round(sobra, 2), "taxa_poupanca": round(taxa * 100)},
            "recomendacoes": recs[:6]}
