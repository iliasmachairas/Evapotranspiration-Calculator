"""
ERA5 grid visualisation on the QGIS map canvas.

Creates a temporary polygon layer that shows the reanalysis grid cells
around the selected point.  The cell containing the point is highlighted
so the user knows exactly which grid cell the data comes from — and that
any point within the same cell yields the same time-series.

Supported resolutions:
  • ERA5        — 0.25° × 0.25°  (~25 km)
  • ERA5-Land   — 0.10° × 0.10°  (~11 km)
"""

from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
    QgsSimpleFillSymbolLayer,
    QgsRuleBasedRenderer,
    QgsFillSymbol,
)


GRID_LAYER_NAME = "ERA5 Grid"

# number of cells to show in each direction from the active cell
GRID_RADIUS = 4   # → (2 × 4 + 1)² = 81 cells


def snap_to_grid(value: float, resolution: float) -> float:
    """Snap a coordinate to the nearest grid-cell centre."""
    return round(round(value / resolution) * resolution, 6)


def create_or_update_grid(
    lat: float,
    lon: float,
    resolution: float = 0.10,
    existing_layer_id: str = None,
) -> tuple:
    """
    Create (or replace) a temporary vector layer showing the ERA5 grid.

    Parameters
    ----------
    lat, lon : float
        User-selected point in WGS-84 decimal degrees.
    resolution : float
        Grid spacing in degrees (0.25 for ERA5, 0.10 for ERA5-Land).
    existing_layer_id : str or None
        If a grid layer already exists, pass its id so it gets removed
        before creating a new one.

    Returns
    -------
    (layer_id, snapped_lat, snapped_lon)
        The QGIS layer id and the coordinates of the active cell centre.
    """
    project = QgsProject.instance()

    # remove previous grid layer
    if existing_layer_id:
        old = project.mapLayer(existing_layer_id)
        if old is not None:
            project.removeMapLayer(existing_layer_id)

    # snap to grid
    clat = snap_to_grid(lat, resolution)
    clon = snap_to_grid(lon, resolution)
    half = resolution / 2.0

    # memory layer
    layer = QgsVectorLayer("Polygon?crs=EPSG:4326", GRID_LAYER_NAME, "memory")
    dp = layer.dataProvider()
    dp.addAttributes([
        QgsField("cell_lat", QVariant.Double),
        QgsField("cell_lon", QVariant.Double),
        QgsField("is_active", QVariant.Int),
    ])
    layer.updateFields()

    # build grid features
    features = []
    r = GRID_RADIUS
    for di in range(-r, r + 1):
        for dj in range(-r, r + 1):
            cell_lat = round(clat + di * resolution, 6)
            cell_lon = round(clon + dj * resolution, 6)

            rect = QgsRectangle(
                cell_lon - half, cell_lat - half,
                cell_lon + half, cell_lat + half,
            )

            feat = QgsFeature(layer.fields())
            feat.setGeometry(QgsGeometry.fromRect(rect))
            is_active = 1 if (di == 0 and dj == 0) else 0
            feat.setAttributes([cell_lat, cell_lon, is_active])
            features.append(feat)

    dp.addFeatures(features)
    layer.updateExtents()

    # ---- styling with rule-based renderer ----------------------------
    _apply_style(layer)

    project.addMapLayer(layer, True)

    return layer.id(), clat, clon


def remove_grid(layer_id: str):
    """Remove the grid layer from the project (if it exists)."""
    if layer_id:
        project = QgsProject.instance()
        if project.mapLayer(layer_id):
            project.removeMapLayer(layer_id)


# ------------------------------------------------------------------
# Styling
# ------------------------------------------------------------------

def _apply_style(layer: QgsVectorLayer):
    """Rule-based renderer: gray grid + highlighted active cell."""

    # ---- inactive cells: no fill, thin gray border ---
    sym_inactive = QgsFillSymbol.createSimple({})
    sym_inactive.deleteSymbolLayer(0)
    sfl_inactive = QgsSimpleFillSymbolLayer()
    sfl_inactive.setColor(QColor(0, 0, 0, 0))            # transparent fill
    sfl_inactive.setStrokeColor(QColor(120, 120, 120))    # gray border
    sfl_inactive.setStrokeWidth(0.25)
    sym_inactive.appendSymbolLayer(sfl_inactive)

    # ---- active cell: light blue fill, thicker blue border ---
    sym_active = QgsFillSymbol.createSimple({})
    sym_active.deleteSymbolLayer(0)
    sfl_active = QgsSimpleFillSymbolLayer()
    sfl_active.setColor(QColor(33, 150, 243, 50))         # light blue fill
    sfl_active.setStrokeColor(QColor(21, 101, 192))       # blue border
    sfl_active.setStrokeWidth(0.8)
    sym_active.appendSymbolLayer(sfl_active)

    # build rules
    root = QgsRuleBasedRenderer.Rule(None)

    rule_inactive = QgsRuleBasedRenderer.Rule(sym_inactive)
    rule_inactive.setFilterExpression('"is_active" = 0')
    rule_inactive.setLabel("Grid cell")
    root.appendChild(rule_inactive)

    rule_active = QgsRuleBasedRenderer.Rule(sym_active)
    rule_active.setFilterExpression('"is_active" = 1')
    rule_active.setLabel("Active cell")
    root.appendChild(rule_active)

    renderer = QgsRuleBasedRenderer(root)
    layer.setRenderer(renderer)
    layer.triggerRepaint()
