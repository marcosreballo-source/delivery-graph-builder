"""Roteamento com OR-Tools: CVRP com janelas de horário (CVRPTW) sobre a matriz OSRM.

- Custo minimizado: tempo total de viagem (min).
- Restrições: capacidade por veículo, janela de cada entrega, jornada do CD.
- Entregas inviáveis podem ser descartadas com penalidade alta, para o solver sempre
  devolver um plano e o painel mostrar o que ficou de fora (em vez de "sem solução").
"""

from __future__ import annotations

from dataclasses import dataclass

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from routing.scenario import Scenario

DROP_PENALTY = 100_000


@dataclass
class Route:
    vehicle: int
    stops: list[int]          # ids dos nós, começando e terminando no CD (0)
    arrivals_min: list[int]   # chegada em cada parada (minutos desde 00:00)
    load: int
    distance_m: float
    duration_min: float


@dataclass
class Plan:
    routes: list[Route]
    dropped: list[int]
    total_distance_km: float
    total_duration_min: float
    status: str


def _hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def solve(sc: Scenario, vehicles: int = 3, capacity: int = 15, shift: tuple[int, int] = (7 * 60, 19 * 60),
          time_limit_s: int = 5, use_windows: bool = True) -> Plan:
    n = len(sc.nodes)
    travel = [[int(round(sc.duration_s[i][j] / 60)) for j in range(n)] for i in range(n)]
    service = [node.service_min for node in sc.nodes]

    manager = pywrapcp.RoutingIndexManager(n, vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)

    def time_cb(fi, ti):
        i, j = manager.IndexToNode(fi), manager.IndexToNode(ti)
        return travel[i][j] + service[i]

    transit = routing.RegisterTransitCallback(time_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(transit)

    demand = routing.RegisterUnaryTransitCallback(lambda fi: sc.nodes[manager.IndexToNode(fi)].demand)
    routing.AddDimensionWithVehicleCapacity(demand, 0, [capacity] * vehicles, True, "Capacity")

    horizon = shift[1]
    routing.AddDimension(transit, 120, horizon, False, "Time")  # até 2 h de espera numa parada
    time_dim = routing.GetDimensionOrDie("Time")
    for node in sc.nodes[1:]:
        lo, hi = node.window if use_windows else shift
        time_dim.CumulVar(manager.NodeToIndex(node.id)).SetRange(max(lo, shift[0]), min(hi, shift[1]))
        routing.AddDisjunction([manager.NodeToIndex(node.id)], DROP_PENALTY)
    for v in range(vehicles):
        time_dim.CumulVar(routing.Start(v)).SetRange(*shift)
        time_dim.CumulVar(routing.End(v)).SetRange(*shift)
        routing.AddVariableMinimizedByFinalizer(time_dim.CumulVar(routing.Start(v)))
        routing.AddVariableMinimizedByFinalizer(time_dim.CumulVar(routing.End(v)))

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(time_limit_s)
    sol = routing.SolveWithParameters(params)
    if sol is None:
        return Plan([], list(range(1, n)), 0.0, 0.0, "sem solução")

    routes, visited = [], {0}
    for v in range(vehicles):
        idx = routing.Start(v)
        stops, arrivals, load, dist = [], [], 0, 0.0
        while True:
            node = manager.IndexToNode(idx)
            stops.append(node)
            arrivals.append(sol.Min(time_dim.CumulVar(idx)))
            load += sc.nodes[node].demand
            if routing.IsEnd(idx):
                break
            nxt = sol.Value(routing.NextVar(idx))
            dist += sc.distance_m[node][manager.IndexToNode(nxt)]
            idx = nxt
        if len(stops) > 2:
            visited.update(stops)
            routes.append(Route(v, stops, arrivals, load, dist, arrivals[-1] - arrivals[0]))
    dropped = [i for i in range(1, n) if i not in visited]
    return Plan(
        routes=routes, dropped=dropped,
        total_distance_km=sum(r.distance_m for r in routes) / 1000,
        total_duration_min=sum(r.duration_min for r in routes),
        status="ok" if not dropped else "parcial",
    )


def route_table(sc: Scenario, plan: Plan) -> list[dict]:
    rows = []
    for k, r in enumerate(plan.routes, start=1):
        for stop, t in zip(r.stops, r.arrivals_min):
            node = sc.nodes[stop]
            rows.append({
                "Veículo": k, "Parada": node.name, "Chegada": _hhmm(t),
                "Janela": "—" if node.kind == "depot" else f"{_hhmm(node.window[0])}–{_hhmm(node.window[1])}",
                "Carga": node.demand,
            })
    return rows
