from django.contrib.gis.db.models.functions import LineLocatePoint, Transform
from django.contrib.gis.geos import LineString
from django.contrib.gis.measure import D
from django.contrib.gis.db.models import GeometryField
from django.db.models import ExpressionWrapper, F, FloatField
from django.db.models.functions import Cast

from apps.routing.models import TruckStop


def get_stations_along_route(
    route_coords_list,
    total_distance_miles,
    buffer_miles=5,
):
    """
    Return truck stops within `buffer_miles` of the route, ordered by
    their position along the route.

    `route_coords_list` must contain (longitude, latitude) pairs in
    WGS84 / EPSG:4326.

    `total_distance_miles` must correspond to the same route geometry
    returned by the routing provider.
    """
    if not route_coords_list or len(route_coords_list) < 2:
        raise ValueError("route_coords_list must contain at least 2 coordinate pairs.")
    if total_distance_miles < 0:
        raise ValueError("total_distance_miles must be non-negative.")

    # Build route line in WGS84 (degrees) for the spatial filter
    route_line_wgs84 = LineString(route_coords_list, srid=4326)

    route_line_3857 = route_line_wgs84.transform(3857, clone=True)

    nearby_stops = (
        TruckStop.objects
        .filter(
            location__dwithin=(route_line_wgs84, D(mi=buffer_miles))
        )
        .annotate(
            # Cast Geography to Geometry, then transform to 3857 inline
            route_fraction=LineLocatePoint(
                route_line_3857,
                Transform(Cast("location", GeometryField(srid=4326)), 3857)
            ),
            # Multiply fraction by total distance
            miles_along_route=ExpressionWrapper(
                F("route_fraction") * float(total_distance_miles),
                output_field=FloatField(),
            )
        )
        .values(
            "opis_truckstop_id",
            "truckstop_name",
            "address",
            "city",
            "state",
            "retail_price",
            "location",
            "miles_along_route",
        )
        .order_by("miles_along_route", "retail_price")
    )
    return list(nearby_stops), route_line_wgs84
