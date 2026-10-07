from django.contrib.gis.db import models


class USCity(models.Model):
    city = models.CharField(max_length=100, db_index=True)
    state = models.CharField(max_length=2, db_index=True)
    state_name = models.CharField(max_length=255, db_index=True)
    county = models.CharField(max_length=100, blank=True, null=True)
    point = models.PointField(srid=4326)

    class Meta:
        verbose_name_plural = "US Cities"
        unique_together = ("city", "state", "county")
        indexes = [
            models.Index(fields=['state', 'city']),
            models.Index(fields=['state', 'county', 'city']),
            models.Index(fields=['state_name', 'city']),
        ]

    def __str__(self):
        return f"{self.city}, {self.state}"


class TruckStop(models.Model):
    opis_truckstop_id = models.IntegerField(unique=True, db_index=True)
    truckstop_name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    rack_id = models.IntegerField(blank=True, null=True)
    retail_price = models.DecimalField(max_digits=6, decimal_places=4)

    # PostGIS PointField (SRID 4326 for WGS 84 standard GPS coordinates)
    location = models.PointField(srid=4326, spatial_index=True, geography=True)

    class Meta:
        indexes = [
            models.Index(fields=["retail_price"]),
        ]

    def __str__(self):
        return f"{self.truckstop_name} ({self.city}, {self.state}) - ${self.retail_price}"
