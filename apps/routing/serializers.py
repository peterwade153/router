from rest_framework import serializers

from apps.routing.services.geocoder import resolve_location_to_coords


class LocationSerializer(serializers.Serializer):
    city = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)
    county = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)
    state = serializers.CharField(max_length=50)

    def validate_city(self, value):
        return value.strip().title() if value else None

    def validate_county(self, value):
        return value.strip().title() if value else None

    def validate_state(self, value):
        return value.strip()


class RouteOptimizationRequestSerializer(serializers.Serializer):
    origin = LocationSerializer()
    destination = LocationSerializer()

    def _resolve_and_enrich_location(self, loc_data: dict, field_name: str) -> dict:
        """Helper to resolve, validate, and enrich a location dictionary."""
        city = loc_data.get("city")
        county = loc_data.get("county")
        state = loc_data["state"]

        resolved = resolve_location_to_coords(city=city, state=state, county=county)

        if resolved and resolved.get("ambiguous_counties"):
            ambiguous = resolved.get("ambiguous_counties")
            counties_str = ", ".join(ambiguous)
            raise serializers.ValidationError({
                field_name: (
                    f"Multiple locations found for '{city}, {state}' across counties: "
                    f"{counties_str}. Please provide a '{field_name}_county'."
                )
            })
        if not resolved or not resolved.get("coordinates"):
            loc_str = f"'{city}, {state}'" if city else f"State/County '{county or ''} {state}'"
            raise serializers.ValidationError({
                field_name: f"Could not find valid U.S. location for {loc_str}. Please verify the state, city, or county."
            })

        loc_data.update({
            "city": city or resolved.get("city"),
            "county": county or resolved.get("county"),
            "state": resolved.get("state"),  # Normalized state code
            "coordinates": resolved["coordinates"],
        })
        return loc_data

    def validate(self, data):
        origin = data["origin"]
        destination = data["destination"]

        origin = self._resolve_and_enrich_location(data["origin"], "origin")
        destination = self._resolve_and_enrich_location(data["destination"], "destination")
        if origin["coordinates"] == destination["coordinates"]:
            raise serializers.ValidationError({
                "destination": "The destination location cannot be identical to the origin location."
            })
        return data


class LocationCoordinatesSerializer(serializers.Serializer):
    longitude = serializers.FloatField()
    latitude = serializers.FloatField()


class LocationPointSerializer(serializers.Serializer):
    city = serializers.CharField(allow_null=True, required=False)
    county = serializers.CharField(allow_null=True, required=False)  # Explicitly returned
    state = serializers.CharField()
    coordinates = LocationCoordinatesSerializer()


class FuelStopDetailSerializer(serializers.Serializer):
    truckstop_name = serializers.CharField()
    city = serializers.CharField()
    state = serializers.CharField()
    retail_price = serializers.FloatField()
    gallons_purchased = serializers.FloatField()
    stop_cost_usd = serializers.FloatField()
    miles_along_route = serializers.FloatField()


class FuelOptimizationResultSerializer(serializers.Serializer):
    stops = FuelStopDetailSerializer(many=True)
    total_fuel_cost_usd = serializers.FloatField()
    total_gallons_purchased = serializers.FloatField()
    final_leg_gallons = serializers.FloatField()
    total_stops_count = serializers.IntegerField()


class RouteOptimizationResponseSerializer(serializers.Serializer):
    origin = LocationPointSerializer()
    destination = LocationPointSerializer()
    total_distance_miles = serializers.FloatField()
    route_fallback_used = serializers.BooleanField(default=False)
    fuel_optimization = FuelOptimizationResultSerializer()
    cached = serializers.BooleanField(default=False)
