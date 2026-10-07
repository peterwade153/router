from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class StationNode:
    id: Any
    name: str
    city: str
    state: str
    miles: float
    price: float

    def build_stop_payload(self, leg_distance: float, mpg: float) -> Dict[str, Any]:
        gallons = leg_distance / mpg
        return {
            "opis_truckstop_id": self.id,
            "truckstop_name": self.name,
            "city": self.city,
            "state": self.state,
            "retail_price": round(self.price, 4),
            "gallons_purchased": round(gallons, 2),
            "stop_cost_usd": round(gallons * self.price, 2),
            "miles_along_route": round(self.miles, 2),
            "distance_to_next_stop_miles": round(leg_distance, 2),
        }


def _extract_val(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _normalize_stations(stations: List[Any], total_distance: float) -> List[StationNode]:
    cleaned = []
    for s in stations:
        miles = _extract_val(s, "miles_along_route")
        price = _extract_val(s, "retail_price")

        if miles is None or price is None:
            raise ValueError("Every station must have miles_along_route and retail_price.")

        miles, price = float(miles), float(price)
        if miles < 0 or price < 0:
            raise ValueError(f"Invalid station metrics: miles={miles}, price={price}")
        if miles > total_distance:
            continue

        name = _extract_val(s, "truckstop_name", "Unknown")
        city = _extract_val(s, "city", "")
        state = _extract_val(s, "state", "")
        st_id = _extract_val(s, "opis_truckstop_id") or hash((miles, price, name, city, state))

        cleaned.append(StationNode(st_id, name, city, state, miles, price))

    cleaned.sort(key=lambda x: (x.miles, x.price))
    deduped = []
    for st in cleaned:
        if not deduped or abs(st.miles - deduped[-1].miles) > 1e-6:
            deduped.append(st)

    return deduped


def optimize_fuel_stops(
    stations: List[Any],
    total_distance_miles: float,
    max_range_miles: float = 500.0,
    mpg: float = 10.0,
    initial_fuel_miles: float = 0.0,
) -> Dict[str, Any]:
    """Find the minimum-cost sequence of fuel stops"""
    if mpg <= 0 or max_range_miles <= 0:
        raise ValueError("mpg and max_range_miles must be positive.")
    if initial_fuel_miles < 0:
        raise ValueError("initial_fuel_miles cannot be negative.")

    if total_distance_miles == 0:
        return {
            "feasible": True,
            "failure_reason": None,
            "stops": [],
            "total_fuel_cost_usd": 0.0,
            "total_gallons_purchased": 0.0,
            "final_leg_gallons": 0.0,
            "total_stops_count": 0,
        }

    # Nodes: [Origin, Station 1, ..., Destination]
    valid_stations = _normalize_stations(stations, total_distance_miles)
    nodes: List[Optional[StationNode]] = [None, *valid_stations, None]
    node_miles = [0.0, *(s.miles for s in valid_stations), total_distance_miles]

    n_nodes = len(nodes)
    dest_idx = n_nodes - 1

    # Shortest Path in DAG
    costs = [float("inf")] * n_nodes
    previous: List[Optional[int]] = [None] * n_nodes
    costs[0] = 0.0

    for i in range(n_nodes):
        if costs[i] == float("inf"):
            continue

        curr_mile, curr_station = node_miles[i], nodes[i]

        for j in range(i + 1, n_nodes):
            dist = node_miles[j] - curr_mile
            if dist > max_range_miles:
                break

            if i == 0:  # Origin
                if dist > initial_fuel_miles:
                    continue
                new_cost = costs[i]
            elif curr_station:  # Fuel Station
                new_cost = costs[i] + (dist / mpg) * curr_station.price
            else:
                continue

            if new_cost < costs[j]:
                costs[j] = new_cost
                previous[j] = i

    # Handle Infeasibility
    if costs[dest_idx] == float("inf"):
        return {
            "feasible": False,
            "failure_reason": (
                "No feasible fuel-stop sequence exists. The route has no fuel stations within "
                f"the search buffer, or distance between available stations exceeds {max_range_miles:.2f} miles."
            ),
            "stops": [],
            "total_fuel_cost_usd": 0.0,
            "total_gallons_purchased": 0.0,
            "final_leg_gallons": None,
            "total_stops_count": 0,
        }

    # Reconstruct Path & Build Result Payload
    path = []
    curr: Optional[int] = dest_idx
    while curr is not None:
        path.append(curr)
        curr = previous[curr]
    path.reverse()

    stops = []
    for idx in range(1, len(path) - 1):
        curr_i, next_i = path[idx], path[idx + 1]
        station = nodes[curr_i]
        leg_dist = node_miles[next_i] - node_miles[curr_i]
        if station:
            stops.append(station.build_stop_payload(leg_dist, mpg))

    total_gallons = sum(s["gallons_purchased"] for s in stops)
    final_leg_gallons = (total_distance_miles - node_miles[path[-2]]) / mpg if stops else 0.0

    return {
        "feasible": True,
        "failure_reason": None,
        "stops": stops,
        "total_fuel_cost_usd": round(costs[dest_idx], 2),
        "total_gallons_purchased": round(total_gallons, 2),
        "final_leg_gallons": round(final_leg_gallons, 2),
        "total_stops_count": len(stops),
    }
