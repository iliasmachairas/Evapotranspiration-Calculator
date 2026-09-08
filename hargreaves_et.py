"""
HargreavesETPlugin — main QGIS plugin class for Evapotranspiration Calculator.

Registers a toolbar button and menu entry, manages the map-click tool
and the ERA5 grid overlay, and launches the dialog.
"""

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
)
from qgis.gui import QgsMapToolEmitPoint

from .dialog import HargreavesDialog
from .grid_layer import create_or_update_grid, remove_grid


class HargreavesETPlugin:
    """QGIS plugin entry-point."""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.dlg = None
        self.map_tool = None
        self._prev_map_tool = None
        self._grid_layer_id = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def initGui(self):  # noqa: N802  (QGIS naming convention)
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        self.action = QAction(icon, "Evapotranspiration Calculator", self.iface.mainWindow())
        self.action.setStatusTip(
            "Estimate potential evapotranspiration with the Hargreaves formula"
        )
        self.action.triggered.connect(self.run)

        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&Evapotranspiration Calculator", self.action)

    def unload(self):
        self.iface.removePluginMenu("&Evapotranspiration Calculator", self.action)
        self.iface.removeToolBarIcon(self.action)
        if self.dlg is not None:
            self.dlg.close()
            self.dlg = None
        self._deactivate_map_tool()
        self._remove_grid()

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------
    def run(self):
        if self.dlg is None:
            self.dlg = HargreavesDialog(self.iface, self.iface.mainWindow())
            self.dlg.pick_point_requested.connect(self._activate_map_tool)
            self.dlg.show_grid_requested.connect(self._show_grid)
            self.dlg.hide_grid_requested.connect(self._remove_grid)

        self.dlg.show()
        self.dlg.raise_()
        self.dlg.activateWindow()

    # ------------------------------------------------------------------
    # Map tool for point selection
    # ------------------------------------------------------------------
    def _activate_map_tool(self):
        """Set up a click-on-map tool so the user can pick a point."""
        canvas = self.iface.mapCanvas()
        self._prev_map_tool = canvas.mapTool()

        self.map_tool = QgsMapToolEmitPoint(canvas)
        self.map_tool.canvasClicked.connect(self._on_map_clicked)
        canvas.setMapTool(self.map_tool)

        self.iface.messageBar().pushInfo(
            "Evapotranspiration Calculator",
            "Click on the map to select a point…"
        )

    def _on_map_clicked(self, point, button):
        """Transform the clicked point to WGS-84 and pass to the dialog."""
        canvas_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")

        if canvas_crs != wgs84:
            transform = QgsCoordinateTransform(
                canvas_crs, wgs84, QgsProject.instance()
            )
            point = transform.transform(point)

        lat = point.y()
        lon = point.x()

        if self.dlg is not None:
            self.dlg.set_point(lat, lon)

        self._deactivate_map_tool()

    def _deactivate_map_tool(self):
        if self._prev_map_tool is not None:
            self.iface.mapCanvas().setMapTool(self._prev_map_tool)
            self._prev_map_tool = None
        self.map_tool = None

    # ------------------------------------------------------------------
    # ERA5 grid overlay
    # ------------------------------------------------------------------
    def _show_grid(self, lat: float, lon: float, resolution: float):
        """Create or update the ERA5 grid layer on the map."""
        layer_id, slat, slon = create_or_update_grid(
            lat, lon, resolution, self._grid_layer_id
        )
        self._grid_layer_id = layer_id

    def _remove_grid(self):
        """Remove the grid layer from the map."""
        remove_grid(self._grid_layer_id)
        self._grid_layer_id = None
