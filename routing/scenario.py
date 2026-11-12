"""Cenário de entregas: um CD e pontos fictícios sobre a malha viária.

O CD é o Velopark (Nova Santa Rita/RS), polo logístico público na divisa com Canoas.
Os pontos de entrega são sorteados dentro de Canoas e encaixados no cruzamento mais
próximo do grafo de ruas (`streetgraph`), de onde saem as matrizes de tempo e distância
pelos menores caminhos. Alternativa: `build()` usa o serviço OSRM em vez do grafo local.
Nenhum dado de cliente é usado.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import requests

OSRM = "https://router.project-osrm.org"
DEPOT = {"name": "CD Velopark (Nova Santa Rita/RS)", "lat": -29.865486, "lng": -51.274158}
CANOAS_BBOX = {"lat": (-29.950, -29.875), "lng": (-51.205, -51.135)}


@dataclass
class Node:
    id: int
    name: str
    lat: float
    lng: float
    kind: str                       # depot | delivery
    demand: int = 0
    window: tuple[int, int] = (8 * 60, 18 * 60)   # minutos desde 00:00
    service_min: int = 0


@dataclass
class Scenario:
    nodes: list[Node]
    distance_m: list[list[float]]
    duration_s: list[list[float]]
    source: str = "OSRM / OpenStreetMap"
    meta: dict = field(default_factory=dict)

    def to_json(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def from_json(cls, path: Path) -> "Scenario":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        nodes = [Node(**{**n, "window": tuple(n["window"])}) for n in raw.pop("nodes")]
        return cls(nodes=nodes, **raw)


def sample_points(n: int, seed: int = 42, depot: dict | None = None, bbox: dict | None = None) -> list[Node]:
    """CD + n pontos fictícios sorteados dentro da caixa (lat/lng), com demanda e janela de horário."""
    depot, bbox = depot or DEPOT, bbox or CANOAS_BBOX
    rng = np.random.default_rng(seed)
    nodes = [Node(0, depot["name"], depot["lat"], depot["lng"], "depot")]
    for i in range(1, n + 1):
        start = int(rng.choice([8, 9, 10, 13, 14])) * 60
        nodes.append(Node(
            id=i, name=f"Entrega {i:02d}",
            lat=float(rng.uniform(*bbox["lat"])), lng=float(rng.uniform(*bbox["lng"])),
            kind="delivery", demand=int(rng.integers(1, 4)),
            window=(start, start + int(rng.choice([180, 240, 300]))), service_min=5,
        ))
    return nodes


def osrm_table(nodes: list[Node], session: requests.Session | None = None) -> tuple[list, list, list]:
    """Matriz completa NxN (distância em m, tempo em s) e as coordenadas ajustadas à malha viária."""
    s = session or requests.Session()
    coords = ";".join(f"{n.lng:.6f},{n.lat:.6f}" for n in nodes)
    r = s.get(f"{OSRM}/table/v1/driving/{coords}", params={"annotations": "distance,duration"}, timeout=60)
    r.raise_for_status()
    body = r.json()
    if body.get("code") != "Ok":
        raise RuntimeError(f"OSRM respondeu {body.get('code')}: {body.get('message')}")
    snapped = [w["location"] for w in body["sources"]]
    return body["distances"], body["durations"], snapped


def route_geometry(points: list[tuple[float, float]], session: requests.Session | None = None) -> list[list[float]]:
    """Traçado da rota pelas ruas (lista de [lng, lat]) passando pelos pontos na ordem dada."""
    s = session or requests.Session()
    coords = ";".join(f"{lng:.6f},{lat:.6f}" for lat, lng in points)
    r = s.get(f"{OSRM}/route/v1/driving/{coords}", params={"overview": "full", "geometries": "geojson"}, timeout=60)
    r.raise_for_status()
    return r.json()["routes"][0]["geometry"]["coordinates"]


def build(n: int = 20, seed: int = 42, depot: dict | None = None, bbox: dict | None = None) -> Scenario:
    nodes = sample_points(n, seed, depot, bbox)
    dist, dur, snapped = osrm_table(nodes)
    for node, (lng, lat) in zip(nodes, snapped):
        node.lat, node.lng = round(lat, 6), round(lng, 6)
    return Scenario(nodes=nodes, distance_m=dist, duration_s=dur,
                    meta={"seed": seed, "n_deliveries": n, "bbox": bbox or CANOAS_BBOX})


def build_on_streets(sg, n: int = 20, seed: int = 42, depot: dict | None = None,
                     bbox: dict | None = None) -> tuple[Scenario, dict]:
    """Encaixa CD e entregas no grafo de ruas e calcula as matrizes pelos menores caminhos.

    Retorna o cenário e os caminhos (sequência de cruzamentos) entre cada par de pontos,
    usados para desenhar as arestas do grafo de entregas seguindo as ruas.
    """
    nodes = sample_points(n, seed, depot, bbox)
    street_nodes = []
    for node in nodes:
        sn = sg.nearest_node(node.lat, node.lng)
        street_nodes.append(sn)
        node.lat, node.lng = sg.g.nodes[sn]["lat"], sg.g.nodes[sn]["lng"]
    dist, dur, paths = sg.od_matrices(street_nodes)
    sc = Scenario(nodes=nodes, distance_m=dist, duration_s=dur, source="Grafo de ruas OpenStreetMap",
                  meta={"seed": seed, "n_deliveries": n, "street_nodes": street_nodes})
    return sc, paths


def to_ortools(sc: Scenario) -> dict:
    """Formato de entrada direto para modelos OR-Tools / benchmarks de VRP (matrizes inteiras em m e s)."""
    return {
        "depot": 0,
        "locations": [{"id": n.id, "name": n.name, "lat": n.lat, "lng": n.lng, "type": n.kind,
                       "demand": n.demand, "time_window_min": list(n.window), "service_min": n.service_min}
                      for n in sc.nodes],
        "distance_matrix": [[int(round(x)) for x in row] for row in sc.distance_m],
        "time_matrix": [[int(round(x)) for x in row] for row in sc.duration_s],
        "source": sc.source,
    }


def main(argv: list[str] | None = None) -> Path:
    import argparse
    p = argparse.ArgumentParser(description="Gera um grafo de entregas (CD + pontos) com pesos reais da malha viária (OSRM).")
    p.add_argument("--n", type=int, default=20, help="número de pontos de entrega")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--depot", nargs=2, type=float, metavar=("LAT", "LNG"), help="coordenadas do CD (padrão: Velopark/RS)")
    p.add_argument("--depot-name", default=None)
    p.add_argument("--bbox", nargs=4, type=float, metavar=("LAT_MIN", "LAT_MAX", "LNG_MIN", "LNG_MAX"),
                   help="área onde sortear as entregas (padrão: Canoas/RS)")
    p.add_argument("--engine", choices=["streets", "osrm"], default="streets",
                   help="streets: menores caminhos no grafo de ruas local; osrm: serviço OSRM público")
    p.add_argument("--streets", type=Path, help="grafo de ruas (.json.gz) gerado por routing.streetgraph")
    p.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "scenario.json")
    p.add_argument("--ortools", type=Path, help="também salva no formato OR-Tools neste caminho")
    a = p.parse_args(argv)
    depot = ({"name": a.depot_name or "CD", "lat": a.depot[0], "lng": a.depot[1]} if a.depot else None)
    bbox = {"lat": tuple(a.bbox[:2]), "lng": tuple(a.bbox[2:])} if a.bbox else None
    if a.engine == "streets":
        from routing.streetgraph import STREETS, StreetGraph
        sc, _ = build_on_streets(StreetGraph.load(a.streets or STREETS), a.n, a.seed, depot, bbox)
    else:
        sc = build(a.n, a.seed, depot, bbox)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    sc.to_json(a.out)
    if a.ortools:
        a.ortools.write_text(json.dumps(to_ortools(sc), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(sc.nodes)} nós salvos em {a.out}")
    return a.out


if __name__ == "__main__":
    main()
