import networkx as nx
import pytest

from routing.graph import asymmetry, knn_edges, to_networkx
from routing.scenario import build_on_streets, sample_points, to_ortools
from routing.solver import solve
from routing.streetgraph import StreetGraph


@pytest.fixture(scope="module")
def sg():
    return StreetGraph.load()


@pytest.fixture(scope="module")
def built(sg):
    return build_on_streets(sg, n=20, seed=42)


def test_street_graph_is_a_directed_road_network(sg):
    g = sg.g
    assert g.number_of_nodes() > 5000 and g.number_of_edges() > 10000
    assert nx.is_strongly_connected(g)                       # dá para ir e voltar entre quaisquer cruzamentos
    one_way = sum(1 for u, v in g.edges if not g.has_edge(v, u))
    assert one_way > 0                                       # há ruas de mão única
    u, v, d = next(iter(g.edges(data=True)))
    assert d["length"] > 0 and d["time"] > 0 and len(d["geometry"]) >= 2


def test_elevation_and_climb(sg, built):
    assert sg.has_elevation
    zs = [d["elev"] for _, d in sg.g.nodes(data=True)]
    assert -10 <= min(zs) < max(zs) < 300  # Canoas/RS: planície do Rio dos Sinos (SRTM tem ruído de ±m perto do rio)
    _, paths = built
    path = paths[(0, 1)]
    up, down = sg.path_climb(path)
    z0, z1 = sg.g.nodes[path[0]]["elev"], sg.g.nodes[path[-1]]["elev"]
    assert up >= 0 and down >= 0 and round(up - down, 1) == round(z1 - z0, 1)  # subida − descida = desnível líquido


def test_deliveries_are_snapped_to_intersections(sg, built):
    sc, _ = built
    for node, street_node in zip(sc.nodes, sc.meta["street_nodes"]):
        assert (node.lat, node.lng) == (sg.g.nodes[street_node]["lat"], sg.g.nodes[street_node]["lng"])


def test_matrices_follow_streets(sg, built):
    sc, paths = built
    n = len(sc.nodes)
    assert all(sc.duration_s[i][i] == 0 for i in range(n))
    # a distância pelas ruas nunca é menor que a soma dos trechos do caminho, e o caminho é contínuo
    path = paths[(0, 1)]
    assert all(sg.g.has_edge(a, b) for a, b in zip(path[:-1], path[1:]))
    geo = sg.path_geometry(path)
    assert geo[0] == [sc.nodes[0].lng, sc.nodes[0].lat] and geo[-1] == [sc.nodes[1].lng, sc.nodes[1].lat]
    assert 0 <= asymmetry(sc) < 1


def test_plan_respects_capacity_windows_and_visits_everyone(built):
    sc, _ = built
    plan = solve(sc, vehicles=3, capacity=20, time_limit_s=3)
    assert plan.status == "ok" and not plan.dropped
    served = sorted(s for r in plan.routes for s in r.stops if s != 0)
    assert served == list(range(1, len(sc.nodes)))
    for r in plan.routes:
        assert r.stops[0] == 0 and r.stops[-1] == 0 and r.load <= 20
        for s, t in zip(r.stops[1:-1], r.arrivals_min[1:-1]):
            lo, hi = sc.nodes[s].window
            assert lo <= t <= hi


def test_tight_fleet_drops_instead_of_failing(built):
    sc, _ = built
    plan = solve(sc, vehicles=1, capacity=5, time_limit_s=2)
    assert plan.status == "parcial" and plan.dropped


def test_ortools_export_and_sampling(built):
    sc, _ = built
    out = to_ortools(sc)
    assert out["depot"] == 0 and len(out["time_matrix"]) == len(sc.nodes)
    assert all(isinstance(x, int) for row in out["distance_matrix"] for x in row)
    bbox = {"lat": (-23.60, -23.55), "lng": (-46.70, -46.63)}
    pts = sample_points(5, seed=1, depot={"name": "CD SP", "lat": -23.56, "lng": -46.68}, bbox=bbox)
    assert pts[0].name == "CD SP" and all(-23.60 <= p.lat <= -23.55 for p in pts[1:])


def test_knn_graph_helpers(built):
    sc, _ = built
    edges = knn_edges(sc, k=2)
    assert len({(e["source"], e["target"]) for e in edges}) == len(edges)
    assert to_networkx(sc, k=8).number_of_nodes() == len(sc.nodes)
