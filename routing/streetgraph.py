"""Grafo da malha viária: nós = cruzamentos, arestas = trechos de rua (OpenStreetMap).

Construção (uma vez, precisa de internet e do osmnx):
    python -m routing.streetgraph            # baixa Canoas + Velopark e grava data/canoas_streets.json.gz

Uso no app (sem osmnx, sem rede): carrega o JSON compacto, encaixa CD e entregas no
cruzamento mais próximo e calcula os menores caminhos (Dijkstra) sobre o grafo dirigido.
Ruas de mão única ficam como arestas de um sentido só — por isso ida e volta diferem.
"""

import gzip
import json
from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import numpy as np

DATA = Path(__file__).resolve().parents[1] / "data"
STREETS = DATA / "canoas_streets.json.gz"
# Canoas/RS + o polo logístico Velopark (Nova Santa Rita), onde fica o CD
BBOX = {"south": -29.965, "north": -29.845, "west": -51.290, "east": -51.120}


# ---------------------------------------------------------------- construção (osmnx)
def download(bbox: dict = BBOX, out: Path = STREETS) -> Path:
    import osmnx as ox

    g = ox.graph_from_bbox((bbox["west"], bbox["south"], bbox["east"], bbox["north"]),
                           network_type="drive", simplify=True)
    g = ox.truncate.largest_component(g, strongly=True)  # todo par de nós tem caminho nos dois sentidos
    g = ox.routing.add_edge_speeds(g)                     # km/h por maxspeed ou tipo de via
    g = ox.routing.add_edge_travel_times(g)

    nodes = {int(n): [round(d["y"], 6), round(d["x"], 6)] for n, d in g.nodes(data=True)}
    best: dict[tuple[int, int], dict] = {}
    for u, v, d in g.edges(data=True):
        key = (int(u), int(v))
        if key in best and best[key]["t"] <= d["travel_time"]:
            continue  # multiaresta: fica a mais rápida
        if "geometry" in d:
            coords = [[round(y, 6), round(x, 6)] for x, y in d["geometry"].coords]
        else:
            coords = [nodes[int(u)], nodes[int(v)]]
        hw = d.get("highway", "")
        best[key] = {"u": int(u), "v": int(v), "m": round(float(d["length"]), 1), "t": round(float(d["travel_time"]), 1),
                     "kph": round(float(d["speed_kph"]), 1), "hw": hw[0] if isinstance(hw, list) else hw, "g": coords}
    payload = {"bbox": bbox, "source": "OpenStreetMap via osmnx", "nodes": nodes, "edges": list(best.values())}
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    return out


def add_elevations(path: Path = STREETS, dataset: str = "srtm30m", batch: int = 100) -> int:
    """Altitude de cada cruzamento (SRTM 30 m via OpenTopoData, gratuito; 100 pontos/req, 1 req/s).

    Grava em `elev` dentro do mesmo arquivo. Rode uma vez depois do download.
    """
    import time

    import requests

    with gzip.open(path, "rt", encoding="utf-8") as fh:
        raw = json.load(fh)
    ids = list(raw["nodes"])
    elev: dict[str, float] = dict(raw.get("elev", {}))
    todo = [i for i in ids if i not in elev]
    s = requests.Session()
    for k in range(0, len(todo), batch):
        chunk = todo[k:k + batch]
        locs = "|".join(f"{raw['nodes'][i][0]},{raw['nodes'][i][1]}" for i in chunk)
        for attempt in range(5):
            r = s.get(f"https://api.opentopodata.org/v1/{dataset}", params={"locations": locs}, timeout=60)
            if r.status_code == 200:
                break
            time.sleep(2 ** attempt)
        r.raise_for_status()
        for i, res in zip(chunk, r.json()["results"]):
            elev[i] = round(float(res["elevation"] or 0.0), 1)
        time.sleep(1.1)
    raw["elev"] = elev
    raw["elev_source"] = f"OpenTopoData {dataset}"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(raw, fh, separators=(",", ":"))
    return len(elev)


# ---------------------------------------------------------------- uso (sem osmnx)
@dataclass
class StreetGraph:
    g: nx.DiGraph
    node_ids: np.ndarray
    node_xy: np.ndarray        # [lat, lng]
    edges: list[dict]
    bbox: dict

    @classmethod
    def load(cls, path: Path = STREETS) -> "StreetGraph":
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            raw = json.load(fh)
        g = nx.DiGraph()
        elev = raw.get("elev", {})
        for n, (lat, lng) in raw["nodes"].items():
            g.add_node(int(n), lat=lat, lng=lng, elev=elev.get(n))
        for e in raw["edges"]:
            g.add_edge(e["u"], e["v"], length=e["m"], time=e["t"], kph=e["kph"], geometry=e["g"])
        ids = np.array(list(g.nodes), dtype=np.int64)
        xy = np.array([[g.nodes[n]["lat"], g.nodes[n]["lng"]] for n in ids])
        return cls(g=g, node_ids=ids, node_xy=xy, edges=raw["edges"], bbox=raw["bbox"])

    def nearest_node(self, lat: float, lng: float) -> int:
        """Cruzamento mais próximo (distância equiretangular, suficiente na escala de uma cidade)."""
        k = np.cos(np.radians(lat))
        d2 = (self.node_xy[:, 0] - lat) ** 2 + ((self.node_xy[:, 1] - lng) * k) ** 2
        return int(self.node_ids[int(np.argmin(d2))])

    @property
    def has_elevation(self) -> bool:
        return all(d.get("elev") is not None for _, d in self.g.nodes(data=True))

    def path_climb(self, path: list[int]) -> tuple[float, float]:
        """Subida e descida acumuladas (m) ao longo do caminho, pela altitude dos cruzamentos."""
        up = down = 0.0
        for a, b in zip(path[:-1], path[1:]):
            dz = (self.g.nodes[b]["elev"] or 0.0) - (self.g.nodes[a]["elev"] or 0.0)
            up, down = (up + dz, down) if dz > 0 else (up, down - dz)
        return up, down

    def path_geometry(self, path: list[int]) -> list[list[float]]:
        """Traçado do caminho seguindo a geometria das ruas, como [lng, lat] (formato do pydeck)."""
        coords: list[list[float]] = []
        for u, v in zip(path[:-1], path[1:]):
            seg = self.g.edges[u, v]["geometry"]
            coords.extend([[lng, lat] for lat, lng in (seg if not coords else seg[1:])])
        return coords

    def od_matrices(self, nodes: list[int]) -> tuple[list[list[float]], list[list[float]], dict]:
        """Matrizes de tempo (s) e distância (m) pelo caminho mais rápido, e os caminhos usados."""
        n = len(nodes)
        dur = [[0.0] * n for _ in range(n)]
        dist = [[0.0] * n for _ in range(n)]
        paths: dict[tuple[int, int], list[int]] = {}
        for i, src in enumerate(nodes):
            t, p = nx.single_source_dijkstra(self.g, src, weight="time")
            for j, dst in enumerate(nodes):
                if i == j:
                    continue
                route = p[dst]
                paths[(i, j)] = route
                dur[i][j] = round(t[dst], 1)
                dist[i][j] = round(sum(self.g.edges[a, b]["length"] for a, b in zip(route[:-1], route[1:])), 1)
        return dist, dur, paths


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Baixa a malha viária (OSM) de uma área como grafo dirigido.")
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("SOUTH", "NORTH", "WEST", "EAST"),
                    help="padrão: Canoas/RS + Velopark")
    ap.add_argument("--out", type=Path, default=STREETS)
    ap.add_argument("--elevation-only", action="store_true", help="só adiciona altitudes a um arquivo já baixado")
    a = ap.parse_args()
    box = dict(zip(["south", "north", "west", "east"], a.bbox)) if a.bbox else BBOX
    out = a.out if a.elevation_only else download(box, a.out)
    print(f"altitudes: {add_elevations(out)} cruzamentos")
    sg = StreetGraph.load(out)
    print(f"{sg.g.number_of_nodes()} cruzamentos, {sg.g.number_of_edges()} trechos -> {out} "
          f"({out.stat().st_size / 1e6:.1f} MB)")
