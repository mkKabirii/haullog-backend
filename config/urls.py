from django.urls import path, include
from django.http import JsonResponse

def health_check(request):
    return JsonResponse({"status": "ok", "service": "haullog-backend"})

urlpatterns = [
    path("", health_check),
    path("api/", include("trips.urls")),
]