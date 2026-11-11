from pathlib import Path

import pytest

from routing.graph import asymmetry, knn_edges, to_networkx
from routing.scenario import Scenario
from routing.solver import solve

DATA = Path(__file__).resolve().parents[1] / "data" / "canoas_20.json"


@pytest.fixture(scope="module")
def sc():
    return Scenario.from_json(DATA)


def test_scenario_is_consistent(sc):
    n = len(sc.nodes)
    assert n == 21 and sc.nodes[0].kind == "depot"
    assert len(sc.duration_s) == n and all(len(r) == n for r in sc.duration_s)
    assert all(sc.duration_s[i][i] == 0 for i in range(n))


def test_plan_respects_capacity_windows_and_visits_everyone(sc):
    plan = solve(sc, vehicles=3, capacity=15, time_limit_s=3)
    assert plan.status == "ok" and not plan.dropped
    served = sorted(s for r in plan.routes for s in r.stops if s != 0)
    assert served == list(range(1, len(sc.nodes)))           # cada entrega exatamente uma vez
    for r in plan.routes:
        assert r.stops[0] == 0 and r.stops[-1] == 0
        assert r.load <= 15
        for s, t in zip(r.stops[1:-1], r.arrivals_min[1:-1]):
            lo, hi = sc.nodes[s].window
            assert lo <= t <= hi                               # chega dentro da janela


def test_tight_fleet_drops_instead_of_failing(sc):
    plan = solve(sc, vehicles=1, capacity=5, time_limit_s=2)
    assert plan.status == "parcial" and plan.dropped
    assert all(r.load <= 5 for r in plan.routes)


def test_ortools_export_and_sampling(sc):
    from routing.scenario import sample_points, to_ortools
    out = to_ortools(sc)
    n = len(sc.nodes)
    assert out["depot"] == 0 and len(out["time_matrix"]) == n
    assert all(isinstance(x, int) for row in out["distance_matrix"] for x in row)
    bbox = {"lat": (-23.60, -23.55), "lng": (-46.70, -46.63)}  # outra cidade: São Paulo
    pts = sample_points(5, seed=1, depot={"name": "CD SP", "lat": -23.56, "lng": -46.68}, bbox=bbox)
    assert pts[0].name == "CD SP" and len(pts) == 6
    assert all(-23.60 <= p.lat <= -23.55 and -46.70 <= p.lng <= -46.63 for p in pts[1:])


def test_graph_helpers(sc):
    edges = knn_edges(sc, k=2)
    assert edges and all(e["minutes"] > 0 for e in edges)
    assert len({(e["source"], e["target"]) for e in edges}) == len(edges)  # sem duplicatas
    assert to_networkx(sc, k=8).number_of_nodes() == 21
    assert 0 <= asymmetry(sc) < 1
