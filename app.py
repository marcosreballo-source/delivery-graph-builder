"""Grafo de entregas sobre a malha de ruas: primeiro as ruas viram grafo, depois as entregas entram nele.

Camadas:
  1. Grafo de ruas (OpenStreetMap): nós = cruzamentos, arestas = trechos com comprimento e velocidade.
  2. Entregas encaixadas no cruzamento mais próximo; arestas entre elas = menor caminho pelas ruas.
  3. Rotas otimizadas pelo OR-Tools (CVRP com janelas), desenhadas trecho a trecho.

Rodar: streamlit run app.py
"""

from __future__ import annotations

import networkx as nx
import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st

from routing.graph import asymmetry, knn_edges
from routing.scenario import build_on_streets
from routing.solver import route_table, solve
from routing.streetgraph import StreetGraph

st.set_page_config(page_title="Grafo de Entregas · Canoas/RS", page_icon="🗺️", layout="wide")
PALETTE = [[230, 57, 70], [29, 130, 220], [46, 158, 91], [240, 160, 0], [150, 80, 200], [0, 170, 170]]


@st.cache_resource(show_spinner="Carregando a malha de ruas…")
def streets() -> StreetGraph:
    return StreetGraph.load()


@st.cache_data(show_spinner=False)
def street_layer_df() -> pd.DataFrame:
    """Trechos de rua coloridos pela velocidade (vermelho = lento, verde = rápido)."""
    rows = []
    for e in streets().edges:
        s = min(max((e["kph"] - 20) / 60, 0), 1)
        rows.append({"path": [[lng, lat] for lat, lng in e["g"]],
                     "color": [int(220 - 170 * s), int(80 + 120 * s), 90, 110]})
    return pd.DataFrame(rows)


@st.cache_data(show_spinner="Encaixando as entregas na malha e calculando os menores caminhos…")
def scenario(n: int, seed: int):
    sc, paths = build_on_streets(streets(), n=n, seed=seed)
    return sc, {f"{i}-{j}": p for (i, j), p in paths.items()}


sg = streets()

with st.sidebar:
    st.header("Cenário")
    n_del = st.slider("Entregas", 5, 40, 20)
    seed = st.number_input("Sorteio dos pontos", 1, 999, 42, help="Gera outro conjunto de entregas em Canoas.")
    st.header("Frota")
    vehicles = st.slider("Veículos", 1, 6, 3)
    sc, paths = scenario(n_del, int(seed))
    total_demand = sum(n.demand for n in sc.nodes)
    capacity = st.slider("Capacidade por veículo (volumes)", 5, 60, 18,
                         help=f"Demanda total do cenário: {total_demand} volumes.")
    use_windows = st.toggle("Respeitar janelas de entrega", value=True)
    st.header("Camadas")
    show_streets = st.toggle("Malha de ruas (grafo base)", value=True)
    show_graph = st.toggle("Grafo de entregas (pelas ruas)", value=False)
    k = st.slider("Arestas por entrega (k vizinhos)", 1, 5, 2, disabled=not show_graph)
    show_routes = st.toggle("Rotas otimizadas", value=True)
    st.caption("Malha: OpenStreetMap (osmnx), velocidades por tipo de via, sem trânsito. "
               "Entregas fictícias em Canoas/RS; CD no polo logístico Velopark (Nova Santa Rita/RS).")

plan = solve(sc, vehicles=vehicles, capacity=capacity, use_windows=use_windows)

st.title("🗺️ Grafo de Entregas — Canoas/RS")
st.caption("Primeiro a malha de ruas vira um grafo dirigido (cruzamentos e trechos); depois as entregas são "
           "encaixadas nele e as rotas são resolvidas com OR-Tools sobre os menores caminhos.")

c = st.columns(5)
c[0].metric("Malha", f"{sg.g.number_of_nodes():,} nós".replace(",", "."),
            help=f"{sg.g.number_of_edges():,} trechos de rua".replace(",", "."))
c[1].metric("Entregas", len(sc.nodes) - 1)
c[2].metric("Veículos usados", f"{len(plan.routes)} de {vehicles}")
c[3].metric("Distância total", f"{plan.total_distance_km:.0f} km")
c[4].metric("Não atendidas", len(plan.dropped), help="Entregas que não cabem na frota ou na janela de horário.")
if plan.dropped:
    st.warning(f"{len(plan.dropped)} entrega(s) ficaram de fora: "
               + ", ".join(sc.nodes[i].name for i in plan.dropped)
               + ". Aumente a frota/capacidade ou desligue as janelas.")


def street_path(i: int, j: int) -> list[list[float]]:
    return sg.path_geometry(paths[f"{i}-{j}"])


# ---------------- mapa ----------------
layers = []
if show_streets:
    layers.append(pdk.Layer("PathLayer", street_layer_df(), get_path="path", get_color="color",
                            width_min_pixels=1, width_max_pixels=2))
if show_graph:
    edges = pd.DataFrame(knn_edges(sc, k))
    edges["path"] = edges.apply(lambda e: street_path(int(e.source), int(e.target)), axis=1)
    edges["tooltip"] = edges.apply(lambda e: f"{sc.nodes[e.source].name} → {sc.nodes[e.target].name}: "
                                              f"{e.minutes} min · {e.km} km pelas ruas", axis=1)
    layers.append(pdk.Layer("PathLayer", edges, get_path="path", get_color=[60, 60, 200, 170],
                            width_min_pixels=2, pickable=True))
if show_routes:
    rows = []
    for v, r in enumerate(plan.routes):
        geo: list[list[float]] = []
        for a, b in zip(r.stops[:-1], r.stops[1:]):
            seg = street_path(a, b) if a != b else []
            geo.extend(seg if not geo else seg[1:])
        rows.append({"path": geo, "color": PALETTE[v % len(PALETTE)],
                     "tooltip": f"Veículo {v + 1}: {len(r.stops) - 2} entregas · {r.distance_m / 1000:.1f} km · "
                                f"{r.duration_min:.0f} min · carga {r.load}/{capacity}"})
    layers.append(pdk.Layer("PathLayer", pd.DataFrame(rows), get_path="path", get_color="color",
                            width_min_pixels=4, pickable=True))

nodes_df = pd.DataFrame([{
    "name": n.name, "lng": n.lng, "lat": n.lat,
    "color": [20, 20, 20] if n.kind == "depot" else ([150, 150, 150] if n.id in plan.dropped else [255, 255, 255]),
    "radius": 150 if n.kind == "depot" else 75,
    "label": "CD" if n.kind == "depot" else str(n.id),
    "tooltip": n.name if n.kind == "depot" else
    f"{n.name} · demanda {n.demand} · janela {n.window[0] // 60:02d}h–{n.window[1] // 60:02d}h",
} for n in sc.nodes])
layers += [
    pdk.Layer("ScatterplotLayer", nodes_df, get_position=["lng", "lat"], get_fill_color="color",
              get_line_color=[0, 0, 0], line_width_min_pixels=1, stroked=True, get_radius="radius", pickable=True),
    pdk.Layer("TextLayer", nodes_df, get_position=["lng", "lat"], get_text="label", get_size=12,
              get_color=[0, 0, 0], get_alignment_baseline="'bottom'", get_pixel_offset=[0, -10]),
]
view = pdk.ViewState(latitude=float(nodes_df.lat.mean()), longitude=float(nodes_df.lng.mean()), zoom=11.5)
st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=view, map_style="light",
                         tooltip={"text": "{tooltip}"}), height=580)
st.caption("Malha colorida pela velocidade da via (vermelho = lenta, verde = rápida). "
           "Ligue “Grafo de entregas” para ver as arestas entre as entregas seguindo as ruas.")

# ---------------- detalhes ----------------
tab_routes, tab_graph = st.tabs(["Rotas", "Grafo"])
with tab_routes:
    if plan.routes:
        st.dataframe(pd.DataFrame(route_table(sc, plan)), hide_index=True, width="stretch")
with tab_graph:
    one_way = sum(1 for u, v in sg.g.edges if not sg.g.has_edge(v, u)) / sg.g.number_of_edges()
    km = sum(d["length"] for *_, d in sg.g.edges(data=True)) / 1000
    a, b, cc, dd = st.columns(4)
    a.metric("Cruzamentos / trechos", f"{sg.g.number_of_nodes():,} / {sg.g.number_of_edges():,}".replace(",", "."))
    b.metric("Malha mapeada", f"{km:,.0f} km".replace(",", "."))
    cc.metric("Trechos de mão única", f"{one_way * 100:.0f}%")
    dd.metric("Assimetria ida × volta", f"{asymmetry(sc) * 100:.0f}%",
              help="Diferença média entre o tempo de i→j e j→i entre as entregas: mão única e conversões.")
    st.caption(f"Fortemente conexo: {'sim' if nx.is_strongly_connected(sg.g) else 'não'} "
               "(todo cruzamento alcança qualquer outro respeitando o sentido das ruas).")
    labels = [("CD" if n.kind == "depot" else str(n.id)) for n in sc.nodes]
    mat = pd.DataFrame(np.round(np.array(sc.duration_s) / 60).astype(int), index=labels, columns=labels)
    st.markdown("**Matriz de tempos pelas ruas (min)**: origem nas linhas, destino nas colunas")
    st.dataframe(mat.style.background_gradient(cmap="Blues", axis=None), width="stretch")
