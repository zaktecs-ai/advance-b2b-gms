"""Geographic helpers: point-in-polygon and grid cell generation.

Grid tiling overcomes Google Maps' ~120 results-per-search cap by dividing a
bounding box into km-sized cells (one search per cell). The longitude step is
latitude-adjusted so cells stay roughly square on the ground.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

_KM_PER_DEG_LAT = 111.32


@dataclass
class BoundingBox:
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float


@dataclass
class Cell:
    lat: float
    lon: float

    def as_query(self, keyword: str) -> str:
        return f"{keyword.strip()} near {self.lat:.4f},{self.lon:.4f}"


def point_in_polygon(lat: float, lon: float, polygon: list[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon.

    ``polygon`` is a list of ``(lat, lon)`` rings (outer ring only). We treat
    latitude as the y-axis and longitude as the x-axis. Boundary points are
    considered NOT strictly inside (returns False).
    """
    if len(polygon) < 3:
        return False
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        yi, xi = polygon[i]   # yi = lat, xi = lon
        yj, xj = polygon[j]
        # A horizontal crossing of the ray (lon fixed) when the edge straddles
        # the point's latitude; use > strictly so boundary returns False.
        if (yi > lat) != (yj > lat):
            x_int = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x_int:
                inside = not inside
        j = i
    return inside


def point_in_any_polygon(lat: float, lon: float, polygons: Iterable[list[tuple[float, float]]]) -> bool:
    return any(point_in_polygon(lat, lon, p) for p in polygons)


def _normalize_cell_size(size_km: float) -> float:
    return size_km if size_km > 0 else 1.0


def _lon_step(bbox: BoundingBox, size_km: float) -> float:
    mid = (bbox.min_lat + bbox.max_lat) / 2
    cos_mid = math.cos(math.radians(mid))
    if abs(cos_mid) < 1e-6:
        cos_mid = 1e-6 if cos_mid >= 0 else -1e-6
    return size_km / (_KM_PER_DEG_LAT * cos_mid)


def generate_cells(bbox: BoundingBox, cell_size_km: float) -> list[Cell]:
    """Return the center point of every grid cell covering the bbox."""
    size = _normalize_cell_size(cell_size_km)
    lat_step = size / _KM_PER_DEG_LAT
    lon_step = _lon_step(bbox, size)
    cells: list[Cell] = []
    lat = bbox.min_lat + lat_step / 2
    while lat < bbox.max_lat:
        lon = bbox.min_lon + lon_step / 2
        while lon < bbox.max_lon:
            cells.append(Cell(lat=lat, lon=lon))
            lon += lon_step
        lat += lat_step
    return cells


def _count_steps(span: float, step: float) -> int:
    """Count cells along one axis, matching generate_cells' half-step offset."""
    # generate_cells starts at min + step/2 and advances by step while < max,
    # so the count is floor((span - step/2) / step) + 1 for a positive count.
    n = math.floor((span - step / 2) / step) + 1
    return n if n > 0 else 0


def estimate_cell_count(bbox: BoundingBox, cell_size_km: float) -> int:
    size = _normalize_cell_size(cell_size_km)
    lat_step = size / _KM_PER_DEG_LAT
    lon_step = _lon_step(bbox, size)
    lat_cells = _count_steps(bbox.max_lat - bbox.min_lat, lat_step)
    lon_cells = _count_steps(bbox.max_lon - bbox.min_lon, lon_step)
    return lat_cells * lon_cells


def parse_bounding_box(s: str) -> BoundingBox:
    """Parse "minLat,minLon,maxLat,maxLon" with validation."""
    parts = [p.strip() for p in s.split(",")]
    if len(parts) != 4:
        raise ValueError(f"bounding box must be 'minLat,minLon,maxLat,maxLon', got {s!r}")
    try:
        vals = [float(p) for p in parts]
    except ValueError as e:
        raise ValueError(f"invalid bounding box number: {e}") from e
    min_lat, min_lon, max_lat, max_lon = vals
    if min_lat >= max_lat:
        raise ValueError("minLat must be < maxLat")
    if min_lon >= max_lon:
        raise ValueError("minLon must be < maxLon")
    if not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90):
        raise ValueError("latitude must be within -90..90")
    if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180):
        raise ValueError("longitude must be within -180..180")
    return BoundingBox(min_lat, min_lon, max_lat, max_lon)


def geojson_polygons(feature_collection: dict) -> list[list[tuple[float, float]]]:
    """Extract all polygon coordinates from a GeoJSON FeatureCollection."""
    out: list[list[tuple[float, float]]] = []

    def _handle_geometry(geom: dict) -> None:
        if not geom:
            return
        gtype = geom.get("type")
        coords = geom.get("coordinates")
        if gtype == "Polygon" and coords:
            # Outer ring is coords[0].
            out.append([(lat, lon) for lon, lat in coords[0]])
        elif gtype == "MultiPolygon" and coords:
            for poly in coords:
                out.append([(lat, lon) for lon, lat in poly[0]])
        elif gtype == "Point" and coords:
            lon, lat = coords
            out.append([(lat, lon)])  # degenerate; point_in_polygon returns False

    if feature_collection.get("type") == "FeatureCollection":
        for feat in feature_collection.get("features", []):
            _handle_geometry(feat.get("geometry"))
    elif feature_collection.get("type") == "Feature":
        _handle_geometry(feature_collection.get("geometry"))
    elif "coordinates" in feature_collection:
        _handle_geometry(feature_collection)
    return out
