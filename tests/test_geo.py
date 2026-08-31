"""Geo helpers: point-in-polygon, grid cells, bounding box, geojson."""
from scraper.maps.geo import (
    point_in_polygon, point_in_any_polygon, generate_cells,
    estimate_cell_count, parse_bounding_box, geojson_polygons, BoundingBox,
)


_SQUARE = [(0.0, 0.0), (0.0, 10.0), (10.0, 10.0), (10.0, 0.0)]


def test_point_inside():
    assert point_in_polygon(5.0, 5.0, _SQUARE) is True


def test_point_outside():
    assert point_in_polygon(20.0, 20.0, _SQUARE) is False
    assert point_in_polygon(-5.0, 5.0, _SQUARE) is False


def test_point_in_any():
    assert point_in_any_polygon(5.0, 5.0, [_SQUARE]) is True


def test_generate_cells():
    bbox = BoundingBox(0.0, 0.0, 0.1, 0.1)
    cells = generate_cells(bbox, 3.0)
    assert len(cells) >= 1
    assert all(-90 <= c.lat <= 90 for c in cells)


def test_estimate_cell_count_matches():
    bbox = BoundingBox(0.0, 0.0, 0.2, 0.2)
    n = estimate_cell_count(bbox, 3.0)
    cells = generate_cells(bbox, 3.0)
    assert n == len(cells)


def test_parse_bounding_box_ok():
    bbox = parse_bounding_box("40.0,-3.8,40.5,-3.6")
    assert bbox.min_lat == 40.0 and bbox.max_lon == -3.6


def test_parse_bounding_box_invalid():
    import pytest
    with pytest.raises(ValueError):
        parse_bounding_box("40.0,-3.8")  # wrong arity
    with pytest.raises(ValueError):
        parse_bounding_box("40.5,-3.8,40.0,-3.6")  # minLat >= maxLat


def test_geojson_polygons():
    fc = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]]},
        }],
    }
    polys = geojson_polygons(fc)
    assert len(polys) == 1
    assert point_in_polygon(5.0, 5.0, polys[0]) is True


def test_grid_queries_have_keyword():
    cells = generate_cells(BoundingBox(0.0, 0.0, 0.05, 0.05), 1.0)
    q = cells[0].as_query("dentist")
    assert "dentist" in q and "near" in q
