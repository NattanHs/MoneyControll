"""Limiares do retrato financeiro e da Saúde, num só lugar.

Eles são convenções de mercado, não verdades: servem de ponto de partida e podem ser ajustados aqui,
sem mexer na lógica. Cada um diz de onde vem.
"""
from __future__ import annotations

# Histórico usado para ler a situação da pessoa
HISTORICO_MESES = 12          # janela máxima, em meses fechados
MIN_MESES = 3                 # com menos que isso não há retrato (o app continua como antes)

# Renda: variação mensal (desvio-padrão / média)
RENDA_CV_ESTAVEL = 0.10       # abaixo disso, renda estável (CLT, servidor)
RENDA_CV_IRREGULAR = 0.30     # acima disso, renda irregular (autônomo, MEI, comissão)
MES_FORTE = 1.15              # receita do mês acima de 115% da renda-base conta como mês forte

# Folga: (renda - gasto) / renda
FOLGA_APERTADO = 0.05         # abaixo disso, aperto
FOLGA_FOLGADO = 0.15          # meta clássica de poupança (15% da renda)
META_APERTADO = 0.05          # primeira meta realista para quem está apertado
MESES_NEGATIVOS_FRACAO = 0.25  # 3 meses negativos em 12
MESES_NEGATIVOS_MIN = 2

# Dívida: parcelas + juros/rotativo/atrasos sobre a renda (PEIC/CNC, maio de 2026: média de 29,3%)
DIVIDA_JANELA = 3             # olha os últimos meses
DIVIDA_RECORRENTE_MIN = 2     # dívida cara presente em pelo menos 2 desses meses
COMPROMETIMENTO_ALTO = 0.30   # referência de mercado: até 30% da renda em dívidas
COMPROMETIMENTO_CRITICO = 0.50  # mais da metade da renda: 18,7% das famílias (PEIC, maio de 2026)

# Saúde do mês (inalterados em relação à versão anterior)
META_POUPANCA = 0.15
PESO_CATEGORIA = 0.30
REDUCAO_SUGERIDA = 0.10
