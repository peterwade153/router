from unittest.mock import patch
from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.routing.models import USCity
from apps.routing.services.geocoder import NominatimService


class RouteOptimizationAPITestCase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.city_franklin_venango = USCity.objects.create(
            city="Franklin",
            county="Venango",
            state="PA",
            state_name="Pennsylvania",
            point=Point(-79.837, 41.387, srid=4326),
        )
        cls.city_franklin_cambria = USCity.objects.create(
            city="Franklin",
            county="Cambria",
            state="PA",
            state_name="Pennsylvania",
            point=Point(-78.922, 40.523, srid=4326),
        )
        cls.city_dallas = USCity.objects.create(
            city="Dallas",
            county="Dallas",
            state="TX",
            state_name="Texas",
            point=Point(-96.797, 32.776, srid=4326),
        )
        cls.city_austin = USCity.objects.create(
            city="Austin",
            county="Travis",
            state="TX",
            state_name="Texas",
            point=Point(-97.743, 30.267, srid=4326),
        )

    def setUp(self, mock_geocode=None):
        cache.clear()
        self.url = reverse("route-optimize")

    @patch("apps.routing.views.RoutingService.calculate_optimal_route")
    def test_successful_route_optimization(self, mock_routing):
        """Test a valid request between two unique cities succeeds and returns 200."""
        mock_routing.return_value = {
            "origin": {
                "city": "Dallas",
                "county": "Dallas",
                "state": "TX",
                "state_name": "Texas",
                "coordinates": {"longitude": -96.797, "latitude": 32.776},
            },
            "destination": {
                "city": "Austin",
                "county": "Travis",
                "state": "TX",
                "state_name": "Texas",
                "coordinates": {"longitude": -97.743, "latitude": 30.267},
            },
            "total_distance_miles": 195.5,
            "route_fallback_used": False,
            "fuel_optimization": {
                "total_fuel_cost_usd": 120.50,
                "total_gallons_purchased": 20.03,
                "final_leg_gallons": 21.11,
                "total_stops_count": 3,
                "stops": [],
            },
        }

        payload = {
            "origin": {
                "city": "Dallas",
                "state": "TX",
            },
            "destination": {
                "city": "Austin",
                "state": "TX",
            },
        }

        response = self.client.post(self.url, payload, format="json")
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["cached"])
        self.assertEqual(response.data["total_distance_miles"], 195.5)
        mock_routing.assert_called_once()

    @patch("apps.routing.views.RoutingService.calculate_optimal_route")
    def test_ambiguous_city_without_county_fails(self, mock_routing):
        """Test that a city name with multiple matches across counties fails without county param."""
        payload = {
            "origin": {
                "city": "Franklin",
                "state": "PA",  # Has both Venango and Cambria
            },
            "destination": {
                "city": "Dallas",
                "state": "TX",
            },
        }

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("origin", response.data)
        # Assert precise response payload structure and guidance content
        error_data = response.data["origin"]
        self.assertIn("Venango", str(error_data))
        self.assertIn("Cambria", str(error_data))
        mock_routing.assert_not_called()

    @patch("apps.routing.views.RoutingService.calculate_optimal_route")
    def test_ambiguous_city_with_county_succeeds(self, mock_routing):
        """Test that providing the county resolves city duplication ambiguity successfully."""
        mock_routing.return_value = {
            "origin": {
                "city": "Franklin",
                "county": "Venango",
                "state": "PA",
                "state_name": "Pennsylvania",
                "coordinates": {"longitude": -79.837, "latitude": 41.387},
            },
            "destination": {
                "city": "Dallas",
                "county": "Dallas",
                "state": "TX",
                "state_name": "Texas",
                "coordinates": {"longitude": -96.797, "latitude": 32.776},
            },
            "total_distance_miles": 1250.0,
            "route_fallback_used": True,
            "fuel_optimization": {
                "total_fuel_cost_usd": 850.0,
                "total_gallons_purchased": 20.03,
                "final_leg_gallons": 21.11,
                "total_stops_count": 3,
                "stops": []
            },
        }

        payload = {
            "origin": {
                "city": "Franklin",
                "county": "Venango",
                "state": "PA",
            },
            "destination": {
                "city": "Dallas",
                "state": "TX",
            },
        }

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["origin"]["county"], "Venango")
        mock_routing.assert_called_once()

    def test_identical_origin_and_destination_fails(self, mock_routing_mock=None):
        """Test that matching origin and destination coordinates are blocked with 400."""
        payload = {
            "origin": {
                "city": "Dallas",
                "state": "TX",
            },
            "destination": {
                "city": "Dallas",
                "state": "TX",
            },
        }

        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("destination", response.data)

    def test_missing_required_state_fails(self):
        """Test validation error when state parameter is missing."""
        payload = {
            "origin": {
                "city": "Dallas",
                # missing state
            },
            "destination": {
                "city": "Austin",
                "state": "TX",
            },
        }

        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("origin", response.data)
        self.assertIn("state", response.data["origin"])

    @patch.object(NominatimService, "geocode_location")
    def test_nonexistent_location_fails(self, mock_geocode):
        """Test validation error when location does not exist in the database or via Nominatim fallback."""
        # Nominatim also returns no location
        mock_geocode.return_value = {}
        payload = {
            "origin": {
                "city": "Atlantis",
                "state": "NY",
            },
            "destination": {
                "city": "Dallas",
                "state": "TX",
            },
        }
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("apps.routing.views.RoutingService.calculate_optimal_route")
    def test_redis_cache_hit_behavior(self, mock_routing):
        """Test that subsequent identical requests retrieve results from Redis cache."""
        mock_routing.return_value = {
            "origin": {
                "city": "Dallas",
                "county": "Dallas",
                "state": "TX",
                "state_name": "Texas",
                "coordinates": {"longitude": -96.797, "latitude": 32.776},
            },
            "destination": {
                "city": "Austin",
                "county": "Travis",
                "state": "TX",
                "state_name": "Texas",
                "coordinates": {"longitude": -97.743, "latitude": 30.267},
            },
            "total_distance_miles": 195.5,
            "route_fallback_used": False,
            "fuel_optimization": {
                "total_fuel_cost_usd": 120.50,
                "total_gallons_purchased": 20.03,
                "final_leg_gallons": 21.11,
                "total_stops_count": 3,
                "stops": []
            },
        }

        payload = {
            "origin": {
                "city": "Dallas",
                "state": "Texas",  # Testing state normalization cache key collision match
            },
            "destination": {
                "city": "Austin",
                "state": "TX",
            },
        }

        # First request (Cache Miss)
        res1 = self.client.post(self.url, payload, format="json")
        self.assertEqual(res1.status_code, status.HTTP_200_OK)
        self.assertFalse(res1.data["cached"])
        self.assertEqual(mock_routing.call_count, 1)

        # Second request with alternate state string input ("Texas" vs "TX" resolved format) (Cache Hit)
        payload_alt = {
            "origin": {
                "city": "Dallas",
                "state": "TX",
            },
            "destination": {
                "city": "Austin",
                "state": "TX",
            },
        }
        res2 = self.client.post(self.url, payload_alt, format="json")
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertTrue(res2.data["cached"])
        # Mock call count remains 1 because the second request was served from Redis cache!
        self.assertEqual(mock_routing.call_count, 1)
