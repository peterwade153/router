import logging
from typing import Any, Dict

from django.core.cache import cache
from rest_framework import status, views
from rest_framework.request import Request
from rest_framework.response import Response

from apps.routing.serializers import (
    RouteOptimizationRequestSerializer,
    RouteOptimizationResponseSerializer,
)
from apps.routing.services.routing import RoutingService, RoutingServiceError
from apps.routing.utils.cache_utils import build_route_optimization_cache_key


logger = logging.getLogger(__name__)


class RouteOptimizationAPIView(views.APIView):
    """REST API endpoint to compute least-cost fuel stops for vehicle routes between U.S. cities."""

    CACHE_TTL_SECONDS = 86400  # 24 hours

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Calculates optimal fuel stops along a driving route given an origin and destination city/state."""
        serializer = RouteOptimizationRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = serializer.validated_data
        origin = validated_data["origin"]
        destination = validated_data["destination"]

        cache_key = build_route_optimization_cache_key(
            orig_city=origin["city"],
            orig_county=origin.get("county"),
            orig_state=origin["state"],
            dest_city=destination["city"],
            dest_county=destination.get("county"),
            dest_state=destination["state"],
        )

        cached_response = self._get_cached_route(cache_key)
        if cached_response:
            cached_response["cached"] = True
            return Response(cached_response, status=status.HTTP_200_OK)

        try:
            route_result = RoutingService.calculate_optimal_route(
                origin_state=origin["state"],
                dest_state=destination["state"],
                origin_city=origin["city"],
                origin_county=origin.get("county"),
                dest_city=destination["city"],
                dest_county=destination.get("county"),
                origin_coords=origin.get("coordinates"),
                dest_coords=destination.get("coordinates"),
            )

            response_serializer = RouteOptimizationResponseSerializer(instance=route_result)
            response_data = response_serializer.data
            response_data["cached"] = False
            self._set_cached_route(cache_key, response_data)
            return Response(response_data, status=status.HTTP_200_OK)
        except RoutingServiceError as e:
            logger.error(f"Routing business logic error: {e}")
            return Response(
                {"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.exception("Unhandled exception during route optimization process.")
            return Response(
                {
                    "detail": "An internal server error occurred while calculating the route."
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _get_cached_route(self, cache_key: str) -> Any:
        try:
            return cache.get(cache_key)
        except Exception as cache_err:
            logger.warning(f"Redis cache lookup error for key '{cache_key}': {cache_err}")
            return None

    def _set_cached_route(self, cache_key: str, data: Dict[str, Any]) -> None:
        try:
            cache.set(cache_key, data, timeout=self.CACHE_TTL_SECONDS)
        except Exception as cache_err:
            logger.warning(f"Redis cache set error for key '{cache_key}': {cache_err}")
