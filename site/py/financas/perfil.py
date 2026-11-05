"""Classificação da qualidade dos gastos (uso interno da reserva sugerida; nunca aparece na tela).

Cada saída cai numa faixa, da mais necessária para a menos necessária:
- essencial: o que se paga para viver e funcionar (moradia, contas, mercado, saúde, transporte básico,
  educação, pets, seguros, impostos, filhos);
- outros: neutro e leve (tarifas bancárias, cursos, livros, móveis, reforma, tecnologia, doações);
- lazer: entretenimento e saídas. Necessário só até certo ponto, então pesa pouco;
- alimentacao_fora: restaurante, delivery e fast food. Entra em alimentação (essencial na teoria), mas
  o custo costuma ser alto e recorrente, então pesa mais que o lazer comum;
- superfluo: compras por impulso, apostas, presentes e outros gastos sem função além do necessário;
- divida: juros, rotativo, cheque especial, empréstimos, multas e atrasos. É o pior sinal;
- investimento: dinheiro que vai para investir. Não é gasto: não pesa na qualidade e conta como poupança.

Contextuais: vestuário e eletrônicos não são supérfluos pela categoria, e sim pelo excesso:
- vestuário é supérfluo só na parte que passa de 5% da renda do mês; até lá, é essencial;
- eletrônico (celular, notebook etc.): a troca antes de 36 meses (vida útil) é supérfluo integral.
  Fora isso, segue o teto: até 15% da renda do mês é essencial, o excesso é supérfluo.

Ordem de decisão: 1) categoria exata conhecida; 2) regras por palavra-chave. Sinais de alerta na
descrição (dívida, supérfluo, alimentação fora) valem mesmo dentro de uma categoria essencial.
A comparação ignora maiúsculas e acentos.
"""
from __future__ import annotations

import re
import unicodedata

from .common import ym_diff

CONTEXTUAIS = ("vestuario", "eletronicos")
TETO_VESTUARIO = 0.05      # vestuário acima de 5% da renda do mês é excesso
TETO_ELETRONICO = 0.15     # eletrônico acima de 15% da renda do mês compromete a renda
VIDA_UTIL_MESES = 36       # celular, notebook etc. duram pelo menos 3 anos

PESO = {"essencial": 0.0, "outros": 0.2, "lazer": 0.5, "alimentacao_fora": 0.8,
        "superfluo": 1.0, "divida": 1.5, "investimento": 0.0}

# Categorias comuns de orçamento pessoal (nome normalizado) -> faixa.
CATEGORIAS = {
    # moradia e contas da casa
    "moradia": "essencial", "aluguel": "essencial", "condominio": "essencial", "iptu": "essencial",
    "luz": "essencial", "energia": "essencial", "energia eletrica": "essencial", "agua": "essencial",
    "gas": "essencial", "internet": "essencial", "telefone": "essencial",
    "diarista": "essencial", "limpeza": "essencial", "manutencao da casa": "essencial",
    "reforma": "outros", "moveis": "outros", "decoracao": "lazer",
    # alimentação
    "mercado": "essencial", "supermercado": "essencial", "feira": "essencial", "padaria": "essencial",
    "acougue": "essencial", "hortifruti": "essencial", "alimentacao": "essencial",
    "restaurante": "alimentacao_fora", "delivery": "alimentacao_fora", "lanche": "alimentacao_fora",
    "fast food": "alimentacao_fora", "ifood": "alimentacao_fora", "bebidas": "lazer", "bar": "lazer",
    # transporte
    "transporte": "essencial", "combustivel": "essencial", "gasolina": "essencial", "onibus": "essencial",
    "metro": "essencial", "estacionamento": "essencial", "pedagio": "essencial", "app de transporte": "essencial",
    "manutencao do carro": "essencial", "ipva": "essencial", "seguro do carro": "essencial", "taxi": "essencial",
    # saúde e cuidado
    "saude": "essencial", "plano de saude": "essencial", "consultas": "essencial", "exames": "essencial",
    "farmacia": "essencial", "remedios": "essencial", "dentista": "essencial", "higiene": "essencial",
    "academia": "lazer", "cosmeticos": "superfluo", "salao": "lazer", "barbearia": "lazer",
    # educação e filhos
    "educacao": "essencial", "escola": "essencial", "faculdade": "essencial", "mensalidade": "essencial",
    "creche": "essencial", "material escolar": "essencial", "cursos": "outros", "livros": "outros",
    "filhos": "essencial", "roupas infantis": "essencial", "brinquedos": "superfluo",
    # pets, seguros, impostos
    "pets": "essencial", "racao": "essencial", "veterinario": "essencial", "pet shop": "lazer",
    "seguros": "essencial", "seguro de vida": "essencial", "seguro residencial": "essencial",
    "impostos": "essencial", "taxas": "outros", "tarifas bancarias": "outros",
    # lazer e assinaturas
    "lazer": "lazer", "cinema": "lazer", "viagem": "lazer", "viagens": "lazer", "passeios": "lazer",
    "jogos": "lazer", "hobby": "lazer", "shows": "lazer", "assinaturas": "lazer", "streaming": "lazer",
    # compras e consumo
    "compras": "superfluo", "presentes": "superfluo", "apostas": "superfluo",
    "doacoes": "outros", "tecnologia": "outros",
    "roupas": "vestuario", "vestuario": "vestuario", "calcados": "vestuario", "roupas adultas": "vestuario",
    "eletronicos": "eletronicos", "celular": "eletronicos", "notebook": "eletronicos",
    "computador": "eletronicos", "tablet": "eletronicos", "tv": "eletronicos",
    # dívidas
    "dividas": "divida", "juros": "divida", "rotativo": "divida", "cheque especial": "divida",
    "emprestimo": "divida", "emprestimos": "divida", "consignado": "divida", "multas": "divida",
    "atrasos": "divida",
    # investimento (não é gasto)
    "investimentos": "investimento", "investimento": "investimento", "poupanca": "investimento",
    "reserva": "investimento",
}

# Palavras-chave para descrições e categorias fora da lista acima. Ordem de prioridade.
REGRAS = [
    ("divida", ["juros", "rotativo", "cheque especial", "multa", "atraso", "emprestimo", "consignado"]),
    ("superfluo", ["shopping", "shopee", "shein", "amazon", "mercado livre", "aliexpress", "aposta", "bet",
                   "loteria", "cassino", "presente", "impulso", "acessorio", "cosmetico", "perfume",
                   "compra online", "brinquedo"]),
    ("alimentacao_fora", ["fast food", "ifood", "rappi", "uber eats", "delivery", "lanche", "hamburg",
                          "mc donald", "mcdonald", "burger", "burguer", "pizza", "restaurante", "lanchonete",
                          "subway", "pastel", "bk"]),
    ("lazer", ["lazer", "cinema", "streaming", "netflix", "spotify", "prime video", "disney", "hbo",
               "assinatura", "jogo", "game", "viagem", "passeio", "show", "festa", "cerveja", "academia",
               "hobby", "bar", "boteco"]),
    ("investimento", ["investimento", "aporte", "tesouro", "cdb", "lci", "lca", "fundo", "acoes", "poupanca"]),
    ("essencial", ["moradia", "aluguel", "condominio", "luz", "energia", "agua", "gas", "internet",
                   "telefone", "mercado", "supermercado", "feira", "acougue", "padaria", "farmacia", "saude",
                   "plano de saude", "medic", "consulta", "exame", "transporte", "onibus", "combustivel",
                   "gasolina", "uber", "metro", "estacionamento", "escola", "faculdade", "mensalidade",
                   "imposto", "iptu", "ipva", "seguro", "limpeza", "higiene", "racao", "veterinario",
                   "creche", "pedagio"]),
    ("vestuario", ["roupa", "calcado", "tenis", "sapato", "vestido", "camiseta", "bermuda", "jaqueta"]),
    ("eletronicos", ["celular", "smartphone", "iphone", "notebook", "computador", "tablet", "eletronico"]),
]

ALERTAS = ("divida", "superfluo", "alimentacao_fora")  # valem mesmo dentro de uma categoria essencial


def _norm(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto or "")
    return " ".join("".join(c for c in base if not unicodedata.combining(c)).lower().split())


def _casa(texto: str, palavras) -> bool:
    return any(re.search(r"\b" + re.escape(p) + r"\b", texto) for p in palavras)


def classificar(categoria: str, descricao: str = "") -> str:
    """Faixa de uma saída. Ex.: "Alimentação" é essencial, mas "iFood" dentro dela vira alimentação fora."""
    cat = _norm(categoria)
    texto = f"{cat} {_norm(descricao)}"
    for faixa, palavras in REGRAS:
        if faixa in ALERTAS and _casa(texto, palavras):
            return faixa
    if cat in CATEGORIAS:
        return CATEGORIAS[cat]
    for faixa, palavras in REGRAS:
        if faixa not in ALERTAS and _casa(texto, palavras):
            return faixa
    return "outros"


def _partes(i: dict):
    for parte in i.get("parts") or [{"category": i["category"], "amount": i["amount"]}]:
        yield parte, ("" if i.get("parts") else i["description"])


def _saidas(items: list[dict]):
    return [i for i in items if i["kind"] == "saida" and not i.get("transfer") and not i.get("invoice_payment")]


def faixas(items: list[dict]) -> dict:
    """Total de saídas do mês por faixa, antes da análise contextual.
    Cartão é aberto nas compras; pagamento de fatura não conta."""
    tot = {k: 0.0 for k in (*PESO, *CONTEXTUAIS)}
    for i in _saidas(items):
        for parte, desc in _partes(i):
            tot[classificar(parte["category"], desc)] += parte["amount"]
    return {k: round(v, 2) for k, v in tot.items()}


def compras_eletronicas(items: list[dict]) -> list[tuple[str, float]]:
    """(data, valor) das compras de eletrônico do mês, para medir a frequência de troca."""
    return [(i["date"], parte["amount"]) for i in _saidas(items) for parte, desc in _partes(i)
            if classificar(parte["category"], desc) == "eletronicos"]


def resolver(meses: list[dict]) -> list[dict]:
    """Aplica a análise contextual. `meses` em ordem crescente, cada um com:
    ym, renda, faixas (de faixas()) e compras (de compras_eletronicas()).
    Altera as faixas no lugar e devolve só as chaves de PESO, com vestuário e eletrônico já
    divididos entre essencial e supérfluo."""
    ultima = None  # ym da compra eletrônica anterior, em qualquer mês
    for m in meses:
        f = m["faixas"]
        renda = m.get("renda") or 0.0
        v = f.get("vestuario", 0.0)
        if renda > 0:
            teto = TETO_VESTUARIO * renda
            f["essencial"] = f.get("essencial", 0.0) + min(v, teto)
            f["superfluo"] = f.get("superfluo", 0.0) + max(0.0, v - teto)
        else:
            f["superfluo"] = f.get("superfluo", 0.0) + v  # sem renda no mês, todo excesso conta

        teto_e = TETO_ELETRONICO * renda
        compras = sorted(m.get("compras", []))
        nao_troca = 0.0
        for data, valor in compras:
            ym = data[:7]
            if ultima is not None and ym_diff(ultima, ym) < VIDA_UTIL_MESES:  # troca antes da vida útil
                f["superfluo"] = f.get("superfluo", 0.0) + valor
            else:
                nao_troca += valor
            ultima = ym
        if nao_troca:  # o que não é troca segue a lógica do teto, como o vestuário
            if renda > 0:
                f["essencial"] = f.get("essencial", 0.0) + min(nao_troca, teto_e)
                f["superfluo"] = f.get("superfluo", 0.0) + max(0.0, nao_troca - teto_e)
            else:
                f["superfluo"] = f.get("superfluo", 0.0) + nao_troca

        for k in CONTEXTUAIS:
            f.pop(k, None)
        for k in ("essencial", "superfluo"):
            f[k] = round(f.get(k, 0.0), 2)
    return [m["faixas"] for m in meses]


def essenciais_por_categoria(items: list[dict]) -> dict:
    """Saídas do mês que são essenciais ou dívidas, por categoria (para mostrar de onde vem o custo essencial)."""
    out: dict[str, float] = {}
    for i in _saidas(items):
        for parte, desc in _partes(i):
            if classificar(parte["category"], desc) in ("essencial", "divida"):
                nome = (parte["category"] or "").strip() or "Sem categoria"
                out[nome] = out.get(nome, 0.0) + parte["amount"]
    return {k: round(v, 2) for k, v in out.items()}


NAO_ESSENCIAIS = ("lazer", "alimentacao_fora", "superfluo")


def nao_essenciais_por_categoria(items: list[dict]) -> dict:
    """Gastos não essenciais do mês por categoria: {categoria: {"faixa": ..., "total": ...}}.
    Usado pelo consultor para sugerir cortes (nunca em gastos essenciais)."""
    out: dict[str, dict] = {}
    for i in _saidas(items):
        for parte, desc in _partes(i):
            faixa = classificar(parte["category"], desc)
            if faixa not in NAO_ESSENCIAIS:
                continue
            nome = (parte["category"] or "").strip() or "Sem categoria"
            cur = out.setdefault(nome, {"faixa": faixa, "total": 0.0})
            cur["total"] += parte["amount"]
            if faixa == "superfluo" or (faixa == "alimentacao_fora" and cur["faixa"] == "lazer"):
                cur["faixa"] = faixa
    return {k: {"faixa": v["faixa"], "total": round(v["total"], 2)} for k, v in out.items()}
