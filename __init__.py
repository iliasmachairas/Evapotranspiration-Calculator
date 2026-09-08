"""
Evapotranspiration Calculator — QGIS Plugin
Estimates daily potential evapotranspiration using the Hargreaves formula
and ERA5 temperature data from the Copernicus Climate Data Store (CDS).
"""


def classFactory(iface):  # noqa: N802
    from .hargreaves_et import HargreavesETPlugin
    return HargreavesETPlugin(iface)
