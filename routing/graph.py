"""Utilidades de grafo: arestas do grafo k-vizinhos e métricas de grafo sobre a matriz de tempos."""

from __future__ import annotations

import networkx as nx
import numpy as np

from routing.scenario import Scenario


def knn_edges(sc: Scenario, k: int = 3) -> list[dict]:
    """Arestas para os k vizinhos mais próximos (em tempo) de cada nó; mantém o grafo legível no mapa."""
    dur = np.array(sc.duration_s)
    edges, seen = [], set()
    for i in range(len(sc.nodes)):
        order = [j for j in np.argsort(dur[i]) if j != i][:k]
        for j in order:
            key = (min(i, j), max(i, j))
            if key in seen:
                continue
            seen.add(key)
            edges.append({
                "source": int(i), "target": int(j),
                "from": [sc.nodes[i].lng, sc.nodes[i].lat], "to": [sc.nodes[j].lng, sc.nodes[j].lat],
                "minutes": round(float(dur[i][j]) / 60, 1),
                "km": round(sc.distance_m[i][j] / 1000, 2),
            })
    return edges


def to_networkx(sc: Scenario, k: int = 3) -> nx.Graph:
    g = nx.Graph()
    for n in sc.nodes:
        g.add_node(n.id, name=n.name, kind=n.kind, demand=n.demand)
    for e in knn_edges(sc, k):
        g.add_edge(e["source"], e["target"], weight=e["minutes"], km=e["km"])
    return g


def asymmetry(sc: Scenario) -> float:
    """Assimetria média do tempo (ida x volta). Sentido único e conversões fazem a malha real ser dirigida."""
    d = np.array(sc.duration_s)
    mask = ~np.eye(len(d), dtype=bool)
    return float(np.mean(np.abs(d - d.T)[mask] / np.maximum(d, d.T)[mask]))
