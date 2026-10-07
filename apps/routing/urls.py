from django.urls import path

from apps.routing.views import RouteOptimizationAPIView

urlpatterns = [
    path("optimize-route/", RouteOptimizationAPIView.as_view(), name="route-optimize"),
]