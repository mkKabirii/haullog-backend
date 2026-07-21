from datetime import datetime, timezone

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .serializers import TripRequestSerializer
from .routing import geocode, route, GeocodeError, RoutingError
from .hos_engine import plan_hos


class PlanTripView(APIView):
    """
    POST /api/plan-trip/
    body: {
      "current_location": "Chicago, IL",
      "pickup_location": "Indianapolis, IN",
      "dropoff_location": "Nashville, TN",
      "current_cycle_used": 12.5
    }
    """

    def post(self, request):
        serializer = TripRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        try:
            cur_lat, cur_lon, cur_name = geocode(data["current_location"])
            pu_lat, pu_lon, pu_name = geocode(data["pickup_location"])
            do_lat, do_lon, do_name = geocode(data["dropoff_location"])
        except GeocodeError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            leg_to_pickup = route((cur_lat, cur_lon), (pu_lat, pu_lon))
            leg_to_dropoff = route((pu_lat, pu_lon), (do_lat, do_lon))
        except RoutingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        start_time = datetime.now(timezone.utc)

        plan = plan_hos(
            start_time=start_time,
            cycle_hours_used=data["current_cycle_used"],
            leg_to_pickup=leg_to_pickup,
            leg_to_dropoff=leg_to_dropoff,
            current_location_label=data["current_location"],
            pickup_location_label=data["pickup_location"],
            dropoff_location_label=data["dropoff_location"],
            pickup_latlon=(pu_lat, pu_lon),
            dropoff_latlon=(do_lat, do_lon),
            current_latlon=(cur_lat, cur_lon),
        )

        response = {
            "locations": {
                "current": {"input": data["current_location"], "resolved": cur_name, "lat": cur_lat, "lon": cur_lon},
                "pickup": {"input": data["pickup_location"], "resolved": pu_name, "lat": pu_lat, "lon": pu_lon},
                "dropoff": {"input": data["dropoff_location"], "resolved": do_name, "lat": do_lat, "lon": do_lon},
            },
            "route": {
                "leg_to_pickup": {
                    "distance_miles": round(leg_to_pickup["distance_miles"], 1),
                    "duration_hours": round(leg_to_pickup["duration_hours"], 2),
                    "geometry": leg_to_pickup["geometry"],
                },
                "leg_to_dropoff": {
                    "distance_miles": round(leg_to_dropoff["distance_miles"], 1),
                    "duration_hours": round(leg_to_dropoff["duration_hours"], 2),
                    "geometry": leg_to_dropoff["geometry"],
                },
            },
            "summary": plan["summary"],
            "stops": plan["stops"],
            "daily_logs": plan["daily_logs"],
            "segments": plan["segments"],
        }
        return Response(response, status=status.HTTP_200_OK)
