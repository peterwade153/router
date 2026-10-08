import logging
from pathlib import Path

import pandas as pd
from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.routing.models import TruckStop, USCity
from apps.routing.services.geocoder import NominatimGeocoder

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent


class Command(BaseCommand):
    help = (
        "High-performance production command to seed US Cities reference table, "
        "infer missing fuel stop coordinates via vectorized mapping, and batch-insert "
        "into PostGIS."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--cities-file",
            type=str,
            default=str(BASE_DIR / "datasets" / "us_cities_2.csv"), # https://raw.githubusercontent.com/kelvins/US-Cities-Database/main/csv/us_cities.csv
            help="Path to the US cities reference CSV file.",
        )
        parser.add_argument(
            "--fuel-file",
            type=str,
            default=str(BASE_DIR / "datasets" / "fuel-prices-for-be-assessment.csv"),
            help="Path to the raw fuel prices CSV file.",
        )
        parser.add_argument(
            "--output-enriched",
            type=str,
            default=str(BASE_DIR / "datasets" / "enriched_fuel_prices.csv"),
            help="Path to save the generated audit-ready enriched temporary CSV file.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=2000,
            help="Batch size for database bulk operations.",
        )

    def handle(self, *args, **options) -> None:
        cities_path = options["cities_file"]
        fuel_path = options["fuel_file"]
        output_path = options["output_enriched"]
        batch_size = options["batch_size"]

        try:
            with transaction.atomic():
                self.stdout.write(
                    self.style.NOTICE("Starting high-performance data pipeline...")
                )
                # Step 1: Seed US Cities Reference Data
                df_cities = self._seed_cities(cities_path, batch_size)

                # Step 2: Enrich Fuel Stops & Build Audit File
                df_enriched = self._enrich_fuel_data(fuel_path, df_cities, output_path)

                # Step 3: Seed Truck Stops
                self._seed_truck_stops(df_enriched, batch_size)

                self.stdout.write(
                    self.style.SUCCESS(
                        "Pipeline completed successfully with high throughput."
                    )
                )
        except Exception as e:
            logger.exception("Seeding pipeline failed due to an unexpected error.")
            raise CommandError(f"Data seeding failed: {e}")

    def _seed_cities(self, file_path: str, batch_size: int) -> pd.DataFrame:
        """Loads, cleans, and bulk-creates USCity records using vectorized zipping."""
        if not Path(file_path).exists():
            raise CommandError(f"Critical Error: Cities file missing at {file_path}")

        self.stdout.write(self.style.NOTICE(f"Loading cities from {file_path}..."))
        df = pd.read_csv(file_path)

        required_cols = {"CITY", "COUNTY", "STATE_CODE", "STATE_NAME", "LATITUDE", "LONGITUDE"}
        if not required_cols.issubset(df.columns):
            raise CommandError(
                f"Cities CSV is missing required columns. Found: {list(df.columns)}"
            )

        # Standardize keys
        df["city_clean"] = df["CITY"].astype(str).str.strip().str.title()
        df["state_clean"] = df["STATE_CODE"].astype(str).str.strip().str.upper()
        df["state_name_clean"] = df["STATE_NAME"].astype(str).str.strip().str.title()
        df["county_clean"] = df["COUNTY"].astype(str).str.strip().str.title()

        df.rename(columns={
            "LATITUDE": "latitude",
            "LONGITUDE": "longitude",
        }, inplace=True)

        cities_to_create = [
            USCity(
                city=c,
                state=s,
                state_name=s_name,
                county=cnt,
                point=Point(float(lon), float(lat), srid=4326),
            )
            for c, s, s_name, cnt, lat, lon in zip(
                df["city_clean"],
                df["state_clean"],
                df["state_name_clean"],
                df["county_clean"],
                df["latitude"],
                df["longitude"],
            )
        ]

        USCity.objects.bulk_create(
            cities_to_create, batch_size=batch_size, ignore_conflicts=True
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded USCity reference table ({len(cities_to_create)} records"
                " processed)."
            )
        )
        return df

    def _enrich_fuel_data(
        self, fuel_path: str, df_cities: pd.DataFrame, output_path: str
    ) -> pd.DataFrame:
        """
        Enriches fuel dataset:
        1. Clean and normalize string/numeric fields.
        2. Filter out non-U.S. rows (e.g., Canadian provinces).
        3. DEDUPLICATE STATIONS EARLY: Keep 1 row per opis_truckstop_id (lowest retail price).
        4. Local vector join against USCity reference data.
        5. Geocode remaining unmapped unique stations via NominatimGeocoder.
        6. Apply state centroids as final safety net for any unresolved coordinates.
        """
        if not Path(fuel_path).exists():
            raise CommandError(f"Critical Error: Fuel file missing at {fuel_path}")

        self.stdout.write(self.style.NOTICE(f"Processing fuel dataset: {fuel_path}"))
        df_fuel = pd.read_csv(fuel_path)

        required_fuel_cols = {
            "OPIS Truckstop ID",
            "Truckstop Name",
            "Address",
            "City",
            "State",
            "Rack ID",
            "Retail Price",
        }
        if not required_fuel_cols.issubset(df_fuel.columns):
            raise CommandError(
                f"Fuel CSV is missing required columns. Found: {list(df_fuel.columns)}"
            )

        # Rename columns inplace
        df_fuel.rename(columns={
            "OPIS Truckstop ID": "opis_truckstop_id",
            "Truckstop Name": "truckstop_name",
            "Address": "address",
            "City": "city",
            "State": "state",
            "Rack ID": "rack_id",
            "Retail Price": "retail_price"
        }, inplace=True)

        # Clean & Standardize String/Numeric Fields
        df_fuel["city_clean"] = df_fuel["city"].astype(str).str.strip().str.title()
        df_fuel["state_clean"] = df_fuel["state"].astype(str).str.strip().str.upper()
        df_fuel["retail_price"] = pd.to_numeric(df_fuel["retail_price"], errors="coerce")

        # Filter out non-U.S. rows (e.g. Canadian provinces)
        valid_us_states = set(df_cities["state_clean"].unique())
        initial_count = len(df_fuel)
        df_fuel = df_fuel[df_fuel["state_clean"].isin(valid_us_states)].copy()
        filtered_canada_count = initial_count - len(df_fuel)

        # Sort by price ascending and drop duplicates by opis_truckstop_id BEFORE geocoding
        df_fuel = df_fuel.sort_values(
            by=["opis_truckstop_id", "retail_price"]
        ).drop_duplicates(subset=["opis_truckstop_id"], keep="first")

        # Local Merge against USCity Data
        df_cities_primary = df_cities.drop_duplicates(
            subset=["city_clean", "state_clean"], keep="first"
        )[["city_clean", "state_clean", "latitude", "longitude"]]

        df_merged = pd.merge(
            df_fuel,
            df_cities_primary,
            on=["city_clean", "state_clean"],
            how="left",
        )

        # Delegate Unmapped Rows Geocoder (Unique Stations Only!)
        missing_mask = df_merged["latitude"].isna() | df_merged["longitude"].isna()
        unmapped_indices = df_merged[missing_mask].index

        if len(unmapped_indices) > 0:
            self.stdout.write(
                self.style.WARNING(
                    f"Found {len(unmapped_indices)} unique unmapped stations. Invoking Geocoder..."
                )
            )

            cache_path = str(BASE_DIR / ".cache" / "nominatim_geocache.json")
            geocoder = NominatimGeocoder(cache_file_path=cache_path)

            resolved_count = 0
            cache_hits = 0

            for idx in unmapped_indices:
                row = df_merged.loc[idx]
                
                result = geocoder.geocode_station(
                    station_id=row["opis_truckstop_id"],
                    address=str(row["address"]).strip(),
                    city=row["city_clean"],
                    state=row["state_clean"],
                )

                if result:
                    df_merged.at[idx, "latitude"] = result["lat"]
                    df_merged.at[idx, "longitude"] = result["lon"]
                    resolved_count += 1
                    if result["is_cache_hit"]:
                        cache_hits += 1

            # Persist updated cache back to disk
            geocoder.save_cache()

            self.stdout.write(
                self.style.SUCCESS(
                    f"Geocoding Complete: Resolved {resolved_count}/{len(unmapped_indices)} "
                    f"stations ({cache_hits} served from cache)."
                )
            )

        # Safety Fallback: State Centroid Mapping
        still_missing = df_merged["latitude"].isna() | df_merged["longitude"].isna()
        unresolved_indices = df_merged[still_missing].index
        if still_missing.any():
            unresolved_states = df_merged.loc[unresolved_indices, "state_clean"].value_counts().to_dict()
            self.stdout.write(
                self.style.WARNING(
                    f"⚠️  [Step 6 Fallback Triggered]: {len(unresolved_indices)} station(s) could not be resolved "
                    f"by USCity or Nominatim. Applying State Centroids for states: {unresolved_states}"
                )
            )
            state_centroids = (
                df_cities.groupby("state_clean")[["longitude", "latitude"]]
                .mean()
                .to_dict(orient="index")
            )
            state_lat_map = {st: coords["latitude"] for st, coords in state_centroids.items()}
            state_lon_map = {st: coords["longitude"] for st, coords in state_centroids.items()}

            df_merged["latitude"] = df_merged["latitude"].fillna(
                df_merged["state_clean"].map(state_lat_map)
            )
            df_merged["longitude"] = df_merged["longitude"].fillna(
                df_merged["state_clean"].map(state_lon_map)
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "✅ [Step 6 Skipped]: 0 records required State Centroid fallback. All stations accurately mapped!"
                )
            )

        # Output cleaned CSV
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        df_merged.to_csv(output_path, index=False)

        self.stdout.write(
            self.style.SUCCESS(
                f"Fuel Dataset Prepared Successfully: Excluded {filtered_canada_count} non-US records. "
                f"Processed exactly {len(df_merged)} unique U.S. truck stops."
            )
        )
        return df_merged

    def _seed_truck_stops(self, df_enriched: pd.DataFrame, batch_size: int) -> None:
        """Parses geographic points and bulk creates TruckStop records using fast zip iteration."""
        self.stdout.write(
            self.style.NOTICE("Seeding TruckStop table into PostGIS...")
        )
        stops_to_create = []
        skipped_count = 0

        # Extract clean parallel arrays to loop over securely and swiftly
        opis_ids = df_enriched["opis_truckstop_id"].values
        names = df_enriched["truckstop_name"].values
        addresses = df_enriched["address"].values
        cities = df_enriched["city"].values
        states = df_enriched["state"].values
        racks = (
            df_enriched["rack_id"].values
            if "rack_id" in df_enriched.columns
            else [None] * len(df_enriched)
        )
        prices = df_enriched["retail_price"].values
        lons = df_enriched["longitude"].values
        lats = df_enriched["latitude"].values

        for opis_id, name, addr, city, state, rack, price, lon, lat in zip(
            opis_ids, names, addresses, cities, states, racks, prices, lons, lats
        ):
            try:
                point = Point(float(lon), float(lat), srid=4326)
                stops_to_create.append(
                    TruckStop(
                        opis_truckstop_id=int(opis_id),
                        truckstop_name=str(name),
                        address=str(addr),
                        city=str(city),
                        state=str(state).upper(),
                        rack_id=int(rack) if pd.notna(rack) else None,
                        retail_price=float(price),
                        location=point,
                    )
                )
            except (ValueError, TypeError) as e:
                skipped_count += 1
                logger.warning(
                    f"Skipping truck stop ID {opis_id} due to parsing error: {e}"
                )

        TruckStop.objects.bulk_create(
            stops_to_create, batch_size=batch_size, ignore_conflicts=True
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {len(stops_to_create)} Truck Stops into PostGIS"
                f" (Skipped: {skipped_count} malformed rows)."
            )
        )
