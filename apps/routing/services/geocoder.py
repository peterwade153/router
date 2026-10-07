import logging
from typing import Dict, Any, Optional, List

from django.contrib.gis.db.models.functions import Centroid
from django.contrib.gis.geos import Point
from django.db import IntegrityError, transaction
from django.core.cache import cache
from django.db.models import Q
from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim

from apps.routing.models import USCity

logger = logging.getLogger(__name__)

# Single shared instance across threads to enforce rate limiting properly
_geocoding_service: Optional["NominatimService"] = None


def get_geocoding_service() -> "NominatimService":
    """Returns a module-level singleton instance of the geocoding service."""
    global _geocoding_service
    if _geocoding_service is None:
        _geocoding_service = NominatimService()
    return _geocoding_service


class NominatimService:
    """
    Handles external geocoding requests via Nominatim (OSM) with:
    - Persistent local JSON caching.
    - Rate-limiting (1.1s delay) and exponential retries.
    - Multi-tier structured query fallback.
    """

    def __init__(
        self,
        user_agent: str = "django_truck_route_optimizer_v1.0",
        min_delay_seconds: float = 1.1,
        cache_timeout_seconds: int = 86400 * 30,  # 30 days
    ):
        self.geolocator = Nominatim(user_agent=user_agent)
        self.geocode_rate_limited = RateLimiter(
            self.geolocator.geocode,
            min_delay_seconds=min_delay_seconds,
            max_retries=3,
            error_wait_seconds=3.0,
            swallow_exceptions=True,
        )
        self.cache_timeout = cache_timeout_seconds

    def geocode_location(
        self,
        city: str,
        state: str,
        country: str = "United States",
    ) -> Optional[Dict[str, Any]]:
        """Geocodes a city and state via Nominatim with Django cache integration."""
        cache_key = f"nominatim_geocode_{city}_{state}_{country}".lower().replace(" ", "_")
        cached_result = cache.get(cache_key)
        if cached_result:
            return {**cached_result, "is_cache_hit": True}

        try:
            structured_query = {
                "city": city,
                "state": state,
                "country": country,
            }
            location = self.geocode_rate_limited(structured_query)

            if location and hasattr(location, "raw"):
                lat, lon = location.latitude, location.longitude
                raw_address = location.raw.get("address", {})
                result = {
                    "lat": float(lat),
                    "lon": float(lon),
                    "raw_address": raw_address,
                }
                cache.set(cache_key, result, timeout=self.cache_timeout)
                return {**result, "is_cache_hit": False}
        except Exception as e:
            logger.warning(f"Geocoding error for '{city}, {state}': {e}")

        return None


def _get_state_centroid(state_queryset, norm_state: str) -> Optional[Dict[str, Any]]:
    """Calculates or retrieves from cache the PostGIS centroid for a given state."""
    cache_key = f"state_centroid_{norm_state}".lower()
    cached_coords = cache.get(cache_key)
    if cached_coords:
        return cached_coords

    centroid_result = state_queryset.aggregate(centroid=Centroid("point"))
    centroid_point = centroid_result.get("centroid")

    if centroid_point:
        coords = (centroid_point.x, centroid_point.y)
        cache.set(cache_key, coords, timeout=86400 * 7)  # Cache for 7 days
        return coords
    return None


def resolve_location_to_coords(
    city: Optional[str] = None,
    state: Optional[str] = None,
    county: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Resolves a location (city + state, optional county, or state-only) to geographic coordinates.

    - Supports state input as either 2-letter code ('TX') or full name ('Texas').
    - Optionally filters by county to resolve ambiguity.
    - Falls back to Nominatim API geocoding if local DB lookup fails.
    - Safely persists newly resolved Nominatim locations into the USCity table.
    - Falls back to state centroid if only state is provided.
    """
    if not state:
        return None

    state_filter = Q(state__iexact=state.strip()) | Q(state_name__iexact=state.strip())
    state_record = USCity.objects.filter(state_filter).values("state").first()
    norm_state = state_record["state"] if state_record else state.strip().upper()

    base_queryset = USCity.objects.filter(state_filter)

    if county:
        base_queryset = base_queryset.filter(county__iexact=county.strip())

    # -------------------------------------------------------------------
    # US CITY LOOKUP
    # -------------------------------------------------------------------
    if city:
        city_clean = city.strip()
        matches = list(base_queryset.filter(city__iexact=city_clean))

        if len(matches) == 1 and matches[0].point:
            match = matches[0]
            return {
                "coordinates": (match.point.x, match.point.y),
                "city": match.city,
                "county": match.county,
                "state": match.state,
                "ambiguous_counties": None,
            }

        # Multiple City Matches Found (Ambiguity requires county clarification)
        if len(matches) > 1 and not county:
            counties: List[str] = sorted(list({m.county for m in matches if m.county}))
            return {
                "coordinates": None,
                "city": city_clean,
                "county": None,
                "state": matches[0].state,
                "ambiguous_counties": counties,
            }
        # ---------------------------------------------------------------
        # NOMINATIM FALLBACK LOOKUP
        # ---------------------------------------------------------------
        logger.info(
            f"DB lookup missed for '{city_clean}, {state}'. Falling back to Nominatim geocoder..."
        )
        geocoder = get_geocoding_service()
        result = geocoder.geocode_location(city=city_clean, state=state)

        if result:
            lat = result["lat"]
            lon = result["lon"]
            raw_address = result["raw_address"]
            point = Point(float(lon), float(lat), srid=4326)

            state_name = raw_address.get("state")
            state_code = raw_address.get("state_code", "").upper() or norm_state
            extracted_county = raw_address.get("county") or county

            # Safely create or update DB record to eliminate race conditions
            try:
                with transaction.atomic():
                    city_obj, created = USCity.objects.get_or_create(
                        city__iexact=city_clean,
                        state__iexact=state_code,
                        defaults={
                            "city": city_clean.title(),
                            "state": state_code,
                            "state_name": state_name,
                            "county": extracted_county,
                            "latitude": lat,
                            "longitude": lon,
                            "point": point,
                        },
                    )
            except IntegrityError as e:
                logger.warning(
                    f"Concurrency conflict while saving geocoded city '{city_clean}, {state_code}': {e}"
                )

            return {
                "coordinates": (float(lon), float(lat)),
                "city": city_clean.title(),
                "county": extracted_county,
                "state": state_code,
                "ambiguous_counties": None,
            }

        logger.warning(
            f"Exact and Nominatim lookups failed for location: city='{city}', county='{county}', state='{state}'"
        )
        return None

    if not base_queryset.exists():
        logger.warning(f"Location lookup failed: No records found for state '{state}'")
        return None

    # State-only (or State + County) centroid calculation with caching
    coords = _get_state_centroid(base_queryset, norm_state)
    if coords:
        return {
            "coordinates": coords,
            "city": None,
            "county": county,
            "state": norm_state,
            "ambiguous_counties": None,
        }

    logger.error(f"Failed to compute PostGIS centroid for state selector '{state}'")
    return None
