# Delivery Graph Builder: da malha de ruas ao grafo de entregas

O Delivery Graph Builder transforma a **malha viária real de uma cidade em grafo**: os cruzamentos viram nós e os trechos de rua viram arestas, com comprimento, velocidade e sentido. **Só depois** os pontos de entrega entram nesse grafo. Cada entrega é encaixada no cruzamento mais próximo, e as arestas entre as entregas são os menores caminhos **pelas ruas**. Sobre esse grafo, você testa soluções de roteamento (OR-Tools, CVRP com janelas de horário) e vê tudo no mapa.

![Grafo de entregas em Canoas/RS](docs/grafo_canoas.jpg)

**▶ Demo online:** https://delivery-graph-builder-canoas.streamlit.app/

## Por que

Modelos de roteamento clássicos, de ML ou quânticos precisam de instâncias realistas. Coordenadas com distância em linha reta ignoram rios, rodovias, mão única e conversões. Partindo da malha de ruas, o grafo de entregas herda tudo isso, e as rotas desenhadas são as que um veículo de fato percorreria. É gratuito (OpenStreetMap) e funciona para qualquer cidade.

Ele nasceu no Instituto Eldorado, na frente de QAOA multiobjetivo para roteamento ([arXiv:2610.00168](https://arxiv.org/abs/2610.00168)). Lá, as instâncias começavam em Canoas/RS com 5, 10, 15 e 20 nós e escalavam para outras cidades. O exemplo usa um CD no polo logístico Velopark (Nova Santa Rita/RS) e entregas fictícias em Canoas.

## Como funciona

```mermaid
flowchart LR
    OSM["OpenStreetMap<br/>(osmnx)"] --> SG["Grafo de ruas dirigido<br/>7.921 cruzamentos · 20.540 trechos<br/>comprimento, velocidade, sentido"]
    DEM["SRTM 30 m<br/>(OpenTopoData)"] -->|altitude por cruzamento| SG
    P["CD + entregas<br/>(demanda, janela)"] --> SNAP["Encaixe no cruzamento<br/>mais próximo"]
    SG --> SNAP --> DJ["Dijkstra pelo tempo<br/>matrizes N×N + caminhos"]
    DJ --> OR["OR-Tools<br/>CVRP com janelas"]
    OR --> MAP["Painel: malha + rotas trecho a trecho<br/>km · horas ao volante · subida/descida"]
    DJ --> EXP["Exportação<br/>JSON / formato OR-Tools"]
```

| Camada | O que é |
|---|---|
| **1. Grafo de ruas** | `routing/streetgraph.py` baixa a malha "drive" do OSM, mantém o maior componente fortemente conexo (todo cruzamento alcança qualquer outro respeitando o sentido), estima velocidade por tipo de via, adiciona a **altitude de cada cruzamento** (SRTM 30 m) e grava um JSON compacto de 0,6 MB. O app não precisa de internet nem do osmnx |
| **2. Grafo de entregas** | As entregas são encaixadas nos cruzamentos. As matrizes de tempo e distância saem dos menores caminhos pelo grafo de ruas, e a mão única faz ida e volta diferirem |
| **3. Rotas** | OR-Tools com capacidade, janelas de horário e jornada do CD. Quando a frota não comporta, o solver descarta entregas com penalidade e diz quais, em vez de falhar |
| **Painel** | Indicadores de distância, **horas ao volante** (só deslocamento) e **subida acumulada**. O mapa mostra a malha colorida pela velocidade da via, as arestas entre entregas seguindo as ruas e as rotas por veículo, com tooltip de km, minutos e ↑/↓ metros. Há tabela de paradas com horário de chegada, resumo por veículo (jornada, subida, descida, carga) e heatmap da matriz. O cenário é sorteável, com **5, 10, 20, 30 ou 40 entregas**, e frota e capacidade são configuráveis. Uma caixa de progresso mostra cada etapa enquanto o mapa é montado |

## Rodar

```bash
pip install -r requirements.txt
streamlit run app.py                                   # painel de Canoas
pytest                                                 # 10 testes: malha, altitude, encaixe, caminhos, solver, exportação e app

# outra cidade: baixar a malha (+ altitudes) e gerar um cenário (requer: pip install osmnx)
python -m routing.streetgraph --bbox -23.60 -23.54 -46.72 -46.62 --out data/sp_streets.json.gz
python -m routing.scenario --streets data/sp_streets.json.gz --n 15 \
  --depot -23.5679 -46.6852 --bbox -23.59 -23.55 -46.70 -46.64 --ortools data/sp_15_ortools.json
```

As velocidades são as nominais de cada tipo de via, sem trânsito. Para pesos que variam com o horário, veja o projeto irmão abaixo.

## Projetos relacionados

- [route-time-crawler](https://github.com/marcosreballo-source/route-time-crawler): coleta contínua de tempos de trajeto por horário, para pesos que **variam com o trânsito**.
- [quantum-routing-research](https://github.com/marcosreballo-source/quantum-routing-research): QGAT e QAOA multiobjetivo, que consomem grafos como estes.
