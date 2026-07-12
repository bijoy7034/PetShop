from math import asin, cos, radians, sin, sqrt

_EARTH_RADIUS_M = 6_371_000.0


def haversine_meters(lat1, lng1, lat2, lng2):
    """Great-circle distance in metres between two WGS-84 coordinates."""
    p1 = radians(lat1)
    p2 = radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lng2 - lng1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    c = 2 * asin(sqrt(a))
    return _EARTH_RADIUS_M * c
