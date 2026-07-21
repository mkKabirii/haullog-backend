"""
hos_engine.py
=====================================
A rule-based simulation of FMCSA Hours-of-Service (property-carrying,
70-hour/8-day cycle, no adverse-driving-conditions exception) used to turn
a route (distance + duration) into:

  1. A duty-status timeline (segments) covering the whole trip.
  2. A per-calendar-day breakdown suitable for drawing FMCSA "Driver's
     Daily Log" grids.
  3. A list of stops (pickup, drop-off, 30-min breaks, fuel stops,
     10-hour rests, 34-hour restarts) with an approximate lat/lon so they
     can be plotted on the map.

Rules implemented (per the FMCSA "Interstate Truck Driver's Guide to
Hours of Service", April 2022):
  * 11-hour driving limit per duty day            (Sec. 395.3(a)(3))
  * 14-hour driving window per duty day            (Sec. 395.3(a)(2))
  * 30-minute break required after 8 cumulative
    hours of driving                               (Sec. 395.3(a)(3)(ii))
  * 70-hour / 8-day on-duty limit                  (Sec. 395.3(b))
  * 34-consecutive-hour restart                    (Sec. 395.3(c))
  * 10 consecutive hours off duty resets the
    11-hour / 14-hour clocks
  * 1 hour on-duty (not driving) for pickup, and 1 hour for drop-off
    (assessment assumption)
  * A fuel stop (30 min, on-duty not driving) at least once every
    1,000 miles (assessment assumption)

This is a simulation for planning purposes, not a certified ELD.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

MAX_DRIVE_HOURS_PER_WINDOW = 11.0
MAX_WINDOW_HOURS = 14.0
BREAK_REQUIRED_AFTER_DRIVING_HOURS = 8.0
BREAK_DURATION_HOURS = 0.5
CYCLE_LIMIT_HOURS = 70.0
RESTART_HOURS = 34.0
OFF_DUTY_RESET_HOURS = 10.0
FUEL_INTERVAL_MILES = 1000.0
FUEL_STOP_HOURS = 0.5
PICKUP_HOURS = 1.0
DROPOFF_HOURS = 1.0
FALLBACK_SPEED_MPH = 55.0

STATUS_OFF_DUTY = "OFF_DUTY"
STATUS_SLEEPER = "SLEEPER_BERTH"
STATUS_DRIVING = "DRIVING"
STATUS_ON_DUTY = "ON_DUTY_NOT_DRIVING"


def haversine_miles(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1, math.sqrt(x)))


def point_along_geometry(coords: List[List[float]], target_miles: float) -> Tuple[float, float]:
    """coords is a list of [lon, lat] (GeoJSON order, as OSRM returns).
    Returns (lat, lon) of the point `target_miles` into the polyline."""
    if not coords:
        return (0.0, 0.0)
    pts = [(c[1], c[0]) for c in coords]  # -> (lat, lon)
    if target_miles <= 0:
        return pts[0]
    covered = 0.0
    for i in range(len(pts) - 1):
        seg = haversine_miles(pts[i], pts[i + 1])
        if covered + seg >= target_miles or i == len(pts) - 2:
            if seg == 0:
                return pts[i]
            frac = min(1.0, max(0.0, (target_miles - covered) / seg))
            lat = pts[i][0] + (pts[i + 1][0] - pts[i][0]) * frac
            lon = pts[i][1] + (pts[i + 1][1] - pts[i][1]) * frac
            return (lat, lon)
        covered += seg
    return pts[-1]


class Segment:
    def __init__(self, status: str, start: datetime, end: datetime, location: str, remark: str,
                 lat: Optional[float] = None, lon: Optional[float] = None, miles: float = 0.0):
        self.status = status
        self.start = start
        self.end = end
        self.location = location
        self.remark = remark
        self.lat = lat
        self.lon = lon
        self.miles = miles

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600.0

    def to_dict(self):
        return {
            "status": self.status,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "hours": round(self.hours, 3),
            "location": self.location,
            "remark": self.remark,
            "lat": self.lat,
            "lon": self.lon,
            "miles": round(self.miles, 1),
        }


class HOSSimulator:
    def __init__(self, start_time: datetime, cycle_hours_used: float):
        self.clock = start_time
        self.cycle_used = cycle_hours_used
        self.window_start: Optional[datetime] = None
        self.drive_in_window = 0.0
        self.drive_since_break = 0.0
        self.segments: List[Segment] = []
        self.stops: List[Dict] = []
        self._since_fuel_miles = 0.0

    def _window_elapsed(self) -> float:
        if self.window_start is None:
            return 0.0
        return (self.clock - self.window_start).total_seconds() / 3600.0

    def _append(self, status: str, hours: float, location: str, remark: str,
                lat: Optional[float] = None, lon: Optional[float] = None, miles: float = 0.0):
        if hours <= 1e-9:
            return
        start = self.clock
        end = self.clock + timedelta(hours=hours)
        seg = Segment(status, start, end, location, remark, lat, lon, miles)
        self.segments.append(seg)
        self.clock = end
        if status in (STATUS_DRIVING, STATUS_ON_DUTY):
            self.cycle_used += hours
        if status == STATUS_DRIVING:
            self.drive_in_window += hours
            self.drive_since_break += hours
        if status in (STATUS_ON_DUTY, STATUS_DRIVING) and self.window_start is None:
            self.window_start = start

    def add_on_duty(self, hours: float, location: str, remark: str, lat=None, lon=None):
        if self.window_start is None:
            self.window_start = self.clock
        self._append(STATUS_ON_DUTY, hours, location, remark, lat, lon)
        self.stops.append({
            "type": remark, "location": location, "lat": lat, "lon": lon,
            "arrival": self.segments[-1].start.isoformat(), "departure": self.clock.isoformat(),
        })

    def rest_10(self, location: str, lat=None, lon=None):
        self._append(STATUS_OFF_DUTY, OFF_DUTY_RESET_HOURS, location,
                     "10-hour rest break (resets daily clocks)", lat, lon)
        self.window_start = None
        self.drive_in_window = 0.0
        self.drive_since_break = 0.0
        self.stops.append({
            "type": "10-Hour Rest", "location": location, "lat": lat, "lon": lon,
            "arrival": self.segments[-1].start.isoformat(), "departure": self.clock.isoformat(),
        })

    def restart_34(self, location: str, lat=None, lon=None):
        self._append(STATUS_OFF_DUTY, RESTART_HOURS, location,
                     "34-hour restart (resets 70-hour cycle)", lat, lon)
        self.window_start = None
        self.drive_in_window = 0.0
        self.drive_since_break = 0.0
        self.cycle_used = 0.0
        self.stops.append({
            "type": "34-Hour Restart", "location": location, "lat": lat, "lon": lon,
            "arrival": self.segments[-1].start.isoformat(), "departure": self.clock.isoformat(),
        })

    def short_break(self, location: str, lat=None, lon=None):
        self._append(STATUS_OFF_DUTY, BREAK_DURATION_HOURS, location, "30-minute required break", lat, lon)
        self.drive_since_break = 0.0
        self.stops.append({
            "type": "30-Min Break", "location": location, "lat": lat, "lon": lon,
            "arrival": self.segments[-1].start.isoformat(), "departure": self.clock.isoformat(),
        })

    def fuel_stop(self, location: str, lat=None, lon=None):
        if self.window_start is None:
            self.window_start = self.clock
        self._append(STATUS_ON_DUTY, FUEL_STOP_HOURS, location, "Fuel stop", lat, lon)
        self.stops.append({
            "type": "Fuel Stop", "location": location, "lat": lat, "lon": lon,
            "arrival": self.segments[-1].start.isoformat(), "departure": self.clock.isoformat(),
        })

    def drive_leg(self, distance_miles: float, duration_hours: float, geometry: List[List[float]],
                  leg_label: str):
        if distance_miles <= 0 or duration_hours <= 0:
            return
        avg_speed = distance_miles / duration_hours if duration_hours > 0 else FALLBACK_SPEED_MPH

        remaining_hours = duration_hours
        miles_into_leg = 0.0
        since_fuel_miles = self._since_fuel_miles

        if self.window_start is None:
            self.window_start = self.clock

        guard = 0
        while remaining_hours > 1e-6:
            guard += 1
            if guard > 500:
                break

            here = point_along_geometry(geometry, miles_into_leg)

            if self.cycle_used >= CYCLE_LIMIT_HOURS - 1e-6:
                self.restart_34(f"{leg_label} (mile {round(miles_into_leg)})", here[0], here[1])
                continue

            if self.drive_since_break >= BREAK_REQUIRED_AFTER_DRIVING_HOURS - 1e-6:
                self.short_break(f"{leg_label} (mile {round(miles_into_leg)})", here[0], here[1])
                continue

            if self.drive_in_window >= MAX_DRIVE_HOURS_PER_WINDOW - 1e-6 or \
               self._window_elapsed() >= MAX_WINDOW_HOURS - 1e-6:
                self.rest_10(f"{leg_label} (mile {round(miles_into_leg)})", here[0], here[1])
                continue

            window_left = MAX_WINDOW_HOURS - self._window_elapsed()
            drive_left = MAX_DRIVE_HOURS_PER_WINDOW - self.drive_in_window
            break_left = BREAK_REQUIRED_AFTER_DRIVING_HOURS - self.drive_since_break
            cycle_left = CYCLE_LIMIT_HOURS - self.cycle_used
            fuel_left_miles = FUEL_INTERVAL_MILES - since_fuel_miles
            fuel_left_hours = fuel_left_miles / avg_speed if avg_speed > 0 else remaining_hours

            chunk = min(remaining_hours, window_left, drive_left, break_left, cycle_left, fuel_left_hours)
            chunk = max(chunk, 0.0)
            if chunk <= 1e-6:
                self.short_break(f"{leg_label} (mile {round(miles_into_leg)})", here[0], here[1])
                continue

            chunk_miles = chunk * avg_speed
            start_here = point_along_geometry(geometry, miles_into_leg)
            self._append(STATUS_DRIVING, chunk, leg_label,
                         f"Driving toward {leg_label}", start_here[0], start_here[1], chunk_miles)

            remaining_hours -= chunk
            miles_into_leg += chunk_miles
            since_fuel_miles += chunk_miles

            if since_fuel_miles >= FUEL_INTERVAL_MILES - 1e-6 and remaining_hours > 1e-6:
                here2 = point_along_geometry(geometry, miles_into_leg)
                self.fuel_stop(f"{leg_label} (mile {round(miles_into_leg)})", here2[0], here2[1])
                since_fuel_miles = 0.0

        self._since_fuel_miles = since_fuel_miles


def build_daily_logs(segments: List[Segment]) -> List[Dict]:
    if not segments:
        return []

    pieces = []
    for seg in segments:
        cursor = seg.start
        while cursor < seg.end:
            day_start = datetime(cursor.year, cursor.month, cursor.day, tzinfo=cursor.tzinfo)
            day_end = day_start + timedelta(days=1)
            piece_end = min(seg.end, day_end)
            pieces.append({
                "date": cursor.date().isoformat(),
                "status": seg.status,
                "start_hour": (cursor - day_start).total_seconds() / 3600.0,
                "end_hour": (piece_end - day_start).total_seconds() / 3600.0,
                "location": seg.location,
                "remark": seg.remark,
            })
            cursor = piece_end

    days: Dict[str, List[Dict]] = {}
    for p in pieces:
        days.setdefault(p["date"], []).append(p)

    logs = []
    for date in sorted(days.keys()):
        entries = days[date]
        totals = {STATUS_OFF_DUTY: 0.0, STATUS_SLEEPER: 0.0, STATUS_DRIVING: 0.0, STATUS_ON_DUTY: 0.0}
        for e in entries:
            totals[e["status"]] += (e["end_hour"] - e["start_hour"])
        logs.append({
            "date": date,
            "entries": entries,
            "totals": {k: round(v, 2) for k, v in totals.items()},
            "total_hours": round(sum(totals.values()), 2),
        })
    return logs


def plan_hos(start_time: datetime, cycle_hours_used: float,
             leg_to_pickup: Dict, leg_to_dropoff: Dict,
             current_location_label: str, pickup_location_label: str,
             dropoff_location_label: str,
             pickup_latlon, dropoff_latlon, current_latlon) -> Dict:
    sim = HOSSimulator(start_time, cycle_hours_used)

    if leg_to_pickup["distance_miles"] > 0.1:
        sim.drive_leg(leg_to_pickup["distance_miles"], leg_to_pickup["duration_hours"],
                      leg_to_pickup["geometry"], f"{current_location_label} -> {pickup_location_label}")

    sim.add_on_duty(PICKUP_HOURS, pickup_location_label, "Pickup (loading)",
                     pickup_latlon[0] if pickup_latlon else None,
                     pickup_latlon[1] if pickup_latlon else None)

    if leg_to_dropoff["distance_miles"] > 0.1:
        sim.drive_leg(leg_to_dropoff["distance_miles"], leg_to_dropoff["duration_hours"],
                      leg_to_dropoff["geometry"], f"{pickup_location_label} -> {dropoff_location_label}")

    sim.add_on_duty(DROPOFF_HOURS, dropoff_location_label, "Drop-off (unloading)",
                     dropoff_latlon[0] if dropoff_latlon else None,
                     dropoff_latlon[1] if dropoff_latlon else None)

    daily_logs = build_daily_logs(sim.segments)

    total_driving = sum(s.hours for s in sim.segments if s.status == STATUS_DRIVING)
    total_on_duty = sum(s.hours for s in sim.segments if s.status == STATUS_ON_DUTY)
    total_off_duty = sum(s.hours for s in sim.segments if s.status == STATUS_OFF_DUTY)
    total_trip_hours = (sim.clock - start_time).total_seconds() / 3600.0
    total_miles = leg_to_pickup["distance_miles"] + leg_to_dropoff["distance_miles"]

    n_10hr_rests = sum(1 for s in sim.stops if s["type"] == "10-Hour Rest")
    n_restarts = sum(1 for s in sim.stops if s["type"] == "34-Hour Restart")
    n_fuel = sum(1 for s in sim.stops if s["type"] == "Fuel Stop")
    n_breaks = sum(1 for s in sim.stops if s["type"] == "30-Min Break")

    return {
        "segments": [s.to_dict() for s in sim.segments],
        "daily_logs": daily_logs,
        "stops": sim.stops,
        "summary": {
            "total_miles": round(total_miles, 1),
            "total_trip_hours": round(total_trip_hours, 2),
            "total_driving_hours": round(total_driving, 2),
            "total_on_duty_not_driving_hours": round(total_on_duty, 2),
            "total_off_duty_hours": round(total_off_duty, 2),
            "num_days": len(daily_logs),
            "num_10hr_rests": n_10hr_rests,
            "num_34hr_restarts": n_restarts,
            "num_fuel_stops": n_fuel,
            "num_30min_breaks": n_breaks,
            "ending_cycle_hours_used": round(sim.cycle_used, 2),
            "trip_start": start_time.isoformat(),
            "trip_end": sim.clock.isoformat(),
        },
    }
