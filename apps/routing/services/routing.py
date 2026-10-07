import logging
import math
import threading
from typing import Any, Dict, Optional, Tuple
from django.conf import settings
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from apps.routing.services.geocoder import resolve_location_to_coords
from apps.routing.services.optimizer import optimize_fuel_stops
from apps.routing.services.spatial import get_stations_along_route

logger = logging.getLogger(__name__)

# Thread-local storage container
_thread_locals = threading.local()


class RoutingServiceError(Exception):
    """Custom exception for errors during route geometry calculation or location resolution."""
    pass


class RoutingService:
    METERS_TO_MILES = 0.000621371
    DEFAULT_TIMEOUT_SECONDS = 5.0
    OSRM_ENDPOINT = getattr(
        settings,
        "OSRM_ROUTING_URL",
        "http://router.project-osrm.org/route/v1/driving",
    )

    _session: Optional[requests.Session] = None

    @classmethod
    def _get_http_session(cls) -> requests.Session:
        """Returns a thread-isolated HTTP session with connection pooling and retries."""
        if not hasattr(_thread_locals, "session"):
            session = requests.Session()
            retries = Retry(
                total=2,
                backoff_factor=0.5,
                status_forcelist=[500, 502, 503, 504],
                raise_on_status=False,
            )
            adapter = HTTPAdapter(
                max_retries=retries,
                pool_connections=20,
                pool_maxsize=20,
            )
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            _thread_locals.session = session

        return _thread_locals.session

    @staticmethod
    def _haversine_distance_miles(
        origin_coords: Tuple[float, float], dest_coords: Tuple[float, float]
    ) -> float:
        """Calculates great-circle distance between two (longitude, latitude) tuples in miles."""
        lon1, lat1 = map(math.radians, origin_coords)
        lon2, lat2 = map(math.radians, dest_coords)

        dlon = lon2 - lon1
        dlat = lat2 - lat1

        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        c = 2 * math.asin(math.sqrt(a))
        return round(c * 3958.8, 2)

    @classmethod
    def get_route_geometry(
        cls, origin_coords: Tuple[float, float], dest_coords: Tuple[float, float]
    ) -> Dict[str, Any]:
        """Fetches route distance and polyline coordinates from OSRM, with Haversine fallback."""
        orig_lon, orig_lat = origin_coords
        dest_lon, dest_lat = dest_coords

        url = f"{cls.OSRM_ENDPOINT}/{orig_lon},{orig_lat};{dest_lon},{dest_lat}?overview=full&geometries=geojson"
        session = cls._get_http_session()

        try:
            response = session.get(url, timeout=cls.DEFAULT_TIMEOUT_SECONDS)
            if response.status_code == 200:
                data = response.json()
                routes = data.get("routes", [])
                if routes:
                    primary_route = routes[0]
                    distance_miles = round(
                        primary_route["distance"] * cls.METERS_TO_MILES, 2
                    )
                    coordinates = primary_route["geometry"]["coordinates"]
                    return {
                        "distance_miles": distance_miles,
                        "coordinates": coordinates,
                        "is_fallback": False,
                    }
            logger.warning(
                f"OSRM returned status {response.status_code} or empty routes. Falling back to straight-line."
            )
        except Exception as e:
            logger.error(f"Failed to fetch route geometry from routing engine: {e}. Falling back to straight-line.")

        # Haversine straight-line fallback using tuples
        fallback_miles = cls._haversine_distance_miles(origin_coords, dest_coords)

        return {
            "distance_miles": fallback_miles,
            "coordinates": [[orig_lon, orig_lat], [dest_lon, dest_lat]],
            "is_fallback": True,
        }

    @classmethod
    def calculate_optimal_route(
        cls,
        origin_state: str,
        dest_state: str,
        origin_city: Optional[str] = None,
        origin_county: Optional[str] = None,
        dest_city: Optional[str] = None,
        dest_county: Optional[str] = None,
        origin_coords: Optional[Tuple[float, float]] = None,
        dest_coords: Optional[Tuple[float, float]] = None,
    ) -> Dict[str, Any]:
        """Executes full route calculation and fuel optimization pipeline."""
        if not origin_coords:
            origin_result = resolve_location_to_coords(
                city=origin_city, state=origin_state, county=origin_county
            )
            origin_coords = origin_result.get("coordinates") if origin_result else None

        if not dest_coords:
            dest_result = resolve_location_to_coords(
                city=dest_city, state=dest_state, county=dest_county
            )
            dest_coords = dest_result.get("coordinates") if dest_result else None

        if not origin_coords:
            raise RoutingServiceError(f"Could not resolve coordinates for origin: {origin_city or origin_state}.")
        if not dest_coords:
            raise RoutingServiceError(f"Could not resolve coordinates for destination: {dest_city or dest_state}.")

        # Route geometry fetch
        route_data = cls.get_route_geometry(origin_coords, dest_coords)
        total_distance = route_data["distance_miles"]
        coords_list = route_data["coordinates"]

        # Fuel station spatial search & DP optimization
        nearby_stations, _ = get_stations_along_route(
            coords_list, total_distance_miles=total_distance, buffer_miles=5
        )
        optimization_results = optimize_fuel_stops(
            stations=nearby_stations,
            total_distance_miles=total_distance,
            max_range_miles=500.0,
            mpg=10.0,
            initial_fuel_miles=500.0,
        )

        return {
            "origin": {
                "city": origin_city or f"{origin_state} Centroid",
                "state": origin_state,
                "county": origin_county,
                "coordinates": {
                    "longitude": origin_coords[0],
                    "latitude": origin_coords[1],
                },
            },
            "destination": {
                "city": dest_city or f"{dest_state} Centroid",
                "state": dest_state,
                "county": dest_county,
                "coordinates": {
                    "longitude": dest_coords[0],
                    "latitude": dest_coords[1],
                },
            },
            "total_distance_miles": total_distance,
            "route_fallback_used": route_data["is_fallback"],
            "fuel_optimization": optimization_results,
        }
