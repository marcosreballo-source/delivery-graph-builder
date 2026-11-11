"""Grafo de entregas georreferenciado: malha viária real (OpenStreetMap) + roteamento com OR-Tools.

Rodar (a partir da pasta dashboard/): streamlit run app.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st

from routing.graph import asymmetry, knn_edges, to_networkx
from routing.scenario import Scenario, route_geometry
from routing.solver import route_table, solve

st.set_page_config(page_title="Grafo de Entregas · Canoas/RS", page_icon="🗺️", layout="wide")
DATA = Path(__file__).parent / "data" / "canoas_20.json"
PALETTE = [[230, 57, 70], [29, 130, 220], [46, 158, 91], [240, 160, 0], [150, 80, 200], [0, 170, 170]]


@st.cache_data
def load() -> Scenario:
    return Scenario.from_json(DATA)


@st.cache_data(show_spinner="Traçando as rotas pelas ruas (OSRM)…", ttl=24 * 3600)
def geometry(points: tuple[tuple[float, float], ...]) -> list[list[float]]:
    try:
        return route_geometry(list(points))
    except Exception:
        return [[lng, lat] for lat, lng in points]  # sem rede: linha reta entre as paradas


sc = load()
n_del = len(sc.nodes) - 1
total_demand = sum(n.demand for n in sc.nodes)

with st.sidebar:
    st.header("Frota")
    vehicles = st.slider("Veículos", 1, 6, 3)
    capacity = st.slider("Capacidade por veículo (volumes)", 5, 40, 15,
                         help=f"Demanda total do cenário: {total_demand} volumes.")
    use_windows = st.toggle("Respeitar janelas de entrega", value=True)
    st.divider()
    st.header("Grafo")
    k = st.slider("Arestas por nó (k vizinhos mais próximos)", 1, 8, 3,
                  help="O grafo completo tem n(n−1)/2 arestas; mostrar só os k vizinhos deixa o mapa legível.")
    show_graph = st.toggle("Mostrar grafo", value=True)
    show_routes = st.toggle("Mostrar rotas", value=True)
    st.caption("Distâncias e tempos: OSRM sobre OpenStreetMap. Pontos de entrega fictícios em Canoas/RS; "
               "CD no polo logístico Velopark (Nova Santa Rita/RS).")

plan = solve(sc, vehicles=vehicles, capacity=capacity, use_windows=use_windows)

st.title("🗺️ Grafo de Entregas — Canoas/RS")
st.caption("Malha viária real (OpenStreetMap/OSRM) como grafo dirigido e ponderado, resolvida com OR-Tools (CVRP com janelas de horário).")

c = st.columns(5)
c[0].metric("Entregas", n_del)
c[1].metric("Veículos usados", f"{len(plan.routes)} de {vehicles}")
c[2].metric("Distância total", f"{plan.total_distance_km:.1f} km")
c[3].metric("Jornadas somadas", f"{plan.total_duration_min / 60:.1f} h",
            help="Da saída à volta ao CD, incluindo serviço e esperas pela abertura das janelas.")
c[4].metric("Não atendidas", len(plan.dropped), help="Entregas que não cabem na frota ou na janela de horário.")
if plan.dropped:
    st.warning(f"{len(plan.dropped)} entrega(s) ficaram de fora: "
               + ", ".join(sc.nodes[i].name for i in plan.dropped)
               + ". Aumente a frota/capacidade ou desligue as janelas.")

# ---------------- mapa ----------------
nodes_df = pd.DataFrame([{
    "id": n.id, "name": n.name, "lng": n.lng, "lat": n.lat, "kind": n.kind, "demand": n.demand,
    "window": "—" if n.kind == "depot" else f"{n.window[0] // 60:02d}h–{n.window[1] // 60:02d}h",
    "color": [20, 20, 20] if n.kind == "depot" else ([150, 150, 150] if n.id in plan.dropped else [255, 255, 255]),
    "radius": 140 if n.kind == "depot" else 70,
    "label": "CD" if n.kind == "depot" else str(n.id),
} for n in sc.nodes])

layers = []
if show_graph:
    edges = pd.DataFrame(knn_edges(sc, k))
    tmax = edges.minutes.max()
    edges["color"] = edges.minutes.apply(lambda m: [90, 90, int(120 + 135 * (1 - m / tmax)), 140])
    edges["tooltip"] = edges.apply(lambda e: f"{sc.nodes[e.source].name} ↔ {sc.nodes[e.target].name}: "
                                              f"{e.minutes} min · {e.km} km", axis=1)
    layers.append(pdk.Layer("LineLayer", edges, get_source_position="from", get_target_position="to",
                            get_color="color", get_width=2, pickable=True))
if show_routes:
    paths = []
    for i, r in enumerate(plan.routes):
        pts = tuple((sc.nodes[s].lat, sc.nodes[s].lng) for s in r.stops)
        paths.append({"path": geometry(pts), "color": PALETTE[i % len(PALETTE)],
                      "tooltip": f"Veículo {i + 1}: {len(r.stops) - 2} entregas · {r.distance_m / 1000:.1f} km · "
                                 f"{r.duration_min:.0f} min · carga {r.load}/{capacity}"})
    layers.append(pdk.Layer("PathLayer", pd.DataFrame(paths), get_path="path", get_color="color",
                            width_min_pixels=4, pickable=True))
nodes_df["tooltip"] = nodes_df.apply(
    lambda n: n["name"] if n.kind == "depot" else f"{n['name']} · demanda {n.demand} · janela {n.window}", axis=1)
layers += [
    pdk.Layer("ScatterplotLayer", nodes_df, get_position=["lng", "lat"], get_fill_color="color",
              get_line_color=[0, 0, 0], line_width_min_pixels=1, stroked=True, get_radius="radius", pickable=True),
    pdk.Layer("TextLayer", nodes_df, get_position=["lng", "lat"], get_text="label", get_size=12,
              get_color=[0, 0, 0], get_alignment_baseline="'bottom'", get_pixel_offset=[0, -10]),
]
view = pdk.ViewState(latitude=float(nodes_df.lat.mean()), longitude=float(nodes_df.lng.mean()), zoom=11.3)
st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=view, map_style="light",
                         tooltip={"text": "{tooltip}"}), height=560)

# ---------------- detalhes ----------------
tab_routes, tab_graph = st.tabs(["Rotas", "Grafo"])
with tab_routes:
    if plan.routes:
        st.dataframe(pd.DataFrame(route_table(sc, plan)), hide_index=True, use_container_width=True)
with tab_graph:
    g = to_networkx(sc, k)
    a, b, cc = st.columns(3)
    a.metric("Nós / arestas exibidas", f"{g.number_of_nodes()} / {g.number_of_edges()}")
    b.metric("Assimetria ida × volta", f"{asymmetry(sc) * 100:.0f}%",
             help="Diferença média entre o tempo de i→j e j→i: a malha real é um grafo dirigido.")
    cc.metric("Grafo k-NN conexo?", "sim" if __import__("networkx").is_connected(g) else "não")
    labels = [("CD" if n.kind == "depot" else str(n.id)) for n in sc.nodes]
    mat = pd.DataFrame(np.round(np.array(sc.duration_s) / 60).astype(int), index=labels, columns=labels)
    st.markdown("**Matriz de tempos de viagem (min)** — origem nas linhas, destino nas colunas")
    st.dataframe(mat.style.background_gradient(cmap="Blues", axis=None), use_container_width=True)
