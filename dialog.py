"""
Main dialog for the Hargreaves ET₀ plugin.

Tab 1  –  Settings: data-source selector + optional CDS API key.
Tab 2  –  Point selection · date range · run · chart · CSV export.

All imports are from the Python stdlib, QGIS, or libraries that ship
with every QGIS installation (numpy, pandas, matplotlib).
No external packages required.
"""

import webbrowser

from qgis.PyQt.QtCore import QSettings, QDate, QThread, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QDateEdit,
    QWidget,
)

import pandas as pd

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from .open_meteo_client import download_daily_temperature, OpenMeteoError
from .hargreaves import compute_et0_from_daily
from .grid_layer import snap_to_grid

# Optional CDS fallback — may fail if GDAL NetCDF support is absent
try:
    from .cds_client import download_era5_temperature, CDSDownloadError
    HAS_CDS = True
except ImportError:
    HAS_CDS = False


SETTINGS_KEY = "HargreavesET"

HELP_URL = "https://github.com/iliasmachairas/Evapotranspiration-Calculator"

SOURCE_OPEN_METEO = "Open-Meteo (fast, no API key)"
SOURCE_CDS = "CDS API (official Copernicus, needs API key)"


# ======================================================================
# Background worker
# ======================================================================

class _AnalysisWorker(QThread):
    """Runs download + Hargreaves computation off the GUI thread."""

    progress = pyqtSignal(str)
    finished = pyqtSignal(object)   # pd.DataFrame on success
    error    = pyqtSignal(str)

    def __init__(self, source, api_key, lat, lon, start_date, end_date):
        super().__init__()
        self.source = source
        self.api_key = api_key
        self.lat = lat
        self.lon = lon
        self.start_date = start_date
        self.end_date = end_date

    def run(self):
        try:
            if self.source == SOURCE_OPEN_METEO:
                self._run_open_meteo()
            else:
                self._run_cds()
        except (OpenMeteoError,) as exc:
            self.error.emit(str(exc))
        except Exception as exc:
            self.error.emit(f"Unexpected error: {exc}")

    # ---------- Open-Meteo path ----------
    def _run_open_meteo(self):
        daily_df = download_daily_temperature(
            lat=self.lat,
            lon=self.lon,
            start_date=self.start_date,
            end_date=self.end_date,
            progress_callback=lambda m: self.progress.emit(m),
        )
        self.progress.emit("Computing Hargreaves ET₀…")
        result = compute_et0_from_daily(daily_df, self.lat)
        self.finished.emit(result)

    # ---------- CDS API path ----------
    def _run_cds(self):
        if not HAS_CDS:
            self.error.emit(
                "CDS backend is not available.\n"
                "GDAL NetCDF support may be missing."
            )
            return
        from .hargreaves import compute_daily_et0
        hourly_df = download_era5_temperature(
            api_key=self.api_key,
            lat=self.lat,
            lon=self.lon,
            start_date=self.start_date,
            end_date=self.end_date,
            progress_callback=lambda m: self.progress.emit(m),
        )
        self.progress.emit("Computing Hargreaves ET₀…")
        result = compute_daily_et0(hourly_df, self.lat)
        self.finished.emit(result)


# ======================================================================
# Dialog
# ======================================================================

class HargreavesDialog(QDialog):
    """Two-tab dialog: Settings  |  Analysis & Results."""

    pick_point_requested = pyqtSignal()
    show_grid_requested = pyqtSignal(float, float, float)  # lat, lon, resolution
    hide_grid_requested = pyqtSignal()

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle("Evapotranspiration Calculator  —  Potential Evapotranspiration")
        self.setMinimumSize(780, 620)

        self._result_df = None
        self._worker = None

        self._build_ui()
        self._load_settings()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self.tab_settings = QWidget()
        self._build_settings_tab(self.tab_settings)
        self.tabs.addTab(self.tab_settings, "⚙  Settings")

        self.tab_analysis = QWidget()
        self._build_analysis_tab(self.tab_analysis)
        self.tabs.addTab(self.tab_analysis, "📊  Analysis & Results")

        # --- Bottom bar (always visible, regardless of tab) ---
        bottom_row = QHBoxLayout()
        bottom_row.addStretch()
        self.btn_help = QPushButton("❔  Help")
        self.btn_help.setToolTip("Open the online documentation")
        self.btn_help.clicked.connect(self._open_help)
        bottom_row.addWidget(self.btn_help)
        layout.addLayout(bottom_row)

    def _open_help(self):
        webbrowser.open(HELP_URL)

    # ---------- Tab 1: Settings ----------
    def _build_settings_tab(self, tab):
        vbox = QVBoxLayout(tab)
        vbox.setSpacing(12)

        # --- Data source ---
        grp_src = QGroupBox("📡 Data Source")
        g_src = QVBoxLayout(grp_src)

        g_src.addWidget(QLabel("Select where to fetch ERA5 temperature data:"))
        self.combo_source = QComboBox()
        self.combo_source.addItem(SOURCE_OPEN_METEO)
        if HAS_CDS:
            self.combo_source.addItem(SOURCE_CDS)
        self.combo_source.currentTextChanged.connect(self._on_source_changed)
        g_src.addWidget(self.combo_source)

        src_info = QLabel(
            "<i>Open-Meteo is recommended — instant results, no API key "
            "needed, uses ERA5 reanalysis (1940–present).</i>"
        )
        src_info.setWordWrap(True)
        g_src.addWidget(src_info)
        vbox.addWidget(grp_src)

        # --- CDS API key (only visible when CDS is selected) ---
        self.grp_cds = QGroupBox("🔑 CDS API Key")
        g_cds = QVBoxLayout(self.grp_cds)

        info = QLabel(
            "Required only for the CDS backend.<br>"
            "1. Register at "
            "<a href='https://cds.climate.copernicus.eu'>"
            "cds.climate.copernicus.eu</a><br>"
            "2. Copy your <b>Personal Access Token</b> and paste below."
        )
        info.setOpenExternalLinks(True)
        info.setWordWrap(True)
        g_cds.addWidget(info)

        row = QHBoxLayout()
        row.addWidget(QLabel("API Key:"))
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setPlaceholderText("Paste your CDS token here")
        row.addWidget(self.api_key_input)

        self.btn_toggle_key = QPushButton("Show")
        self.btn_toggle_key.setFixedWidth(60)
        self.btn_toggle_key.clicked.connect(self._toggle_key_visibility)
        row.addWidget(self.btn_toggle_key)
        g_cds.addLayout(row)

        btn_row = QHBoxLayout()
        self.btn_save_key = QPushButton("💾  Save Key")
        self.btn_save_key.clicked.connect(self._save_api_key)
        btn_row.addWidget(self.btn_save_key)
        self.lbl_key_status = QLabel("")
        btn_row.addWidget(self.lbl_key_status)
        btn_row.addStretch()
        g_cds.addLayout(btn_row)

        vbox.addWidget(self.grp_cds)
        self.grp_cds.setVisible(False)  # hidden by default (Open-Meteo)

        vbox.addStretch()

    # ---------- Tab 2: Analysis ----------
    def _build_analysis_tab(self, tab):
        vbox = QVBoxLayout(tab)
        vbox.setSpacing(8)

        # --- Point selection ---
        grp_point = QGroupBox("📍 Point Selection")
        g1_v = QVBoxLayout(grp_point)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Latitude:"))
        self.input_lat = QLineEdit()
        self.input_lat.setPlaceholderText("e.g. 37.98")
        self.input_lat.setFixedWidth(110)
        row1.addWidget(self.input_lat)

        row1.addWidget(QLabel("Longitude:"))
        self.input_lon = QLineEdit()
        self.input_lon.setPlaceholderText("e.g. 23.73")
        self.input_lon.setFixedWidth(110)
        row1.addWidget(self.input_lon)

        self.btn_pick = QPushButton("🖱  Pick on Map")
        self.btn_pick.setToolTip("Click a point on the map canvas")
        self.btn_pick.clicked.connect(self._request_pick_point)
        row1.addWidget(self.btn_pick)
        row1.addStretch()
        g1_v.addLayout(row1)

        # Grid controls
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Grid:"))
        self.combo_grid = QComboBox()
        self.combo_grid.addItem("ERA5-Land  (0.1° ≈ 11 km)", 0.10)
        self.combo_grid.addItem("ERA5  (0.25° ≈ 25 km)", 0.25)
        self.combo_grid.setFixedWidth(200)
        row2.addWidget(self.combo_grid)

        self.btn_grid_toggle = QPushButton("🔲  Show Grid")
        self.btn_grid_toggle.setCheckable(True)
        self.btn_grid_toggle.setToolTip(
            "Toggle the ERA5 grid overlay on the map"
        )
        self.btn_grid_toggle.clicked.connect(self._toggle_grid)
        row2.addWidget(self.btn_grid_toggle)

        self.lbl_cell = QLabel("")
        self.lbl_cell.setStyleSheet("color: #555; font-style: italic;")
        row2.addWidget(self.lbl_cell)
        row2.addStretch()
        g1_v.addLayout(row2)

        vbox.addWidget(grp_point)

        # --- Date range ---
        grp_date = QGroupBox("📅 Date Range")
        g2 = QHBoxLayout(grp_date)

        g2.addWidget(QLabel("From:"))
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDate(QDate(2023, 1, 1))
        self.date_from.setDisplayFormat("yyyy-MM-dd")
        g2.addWidget(self.date_from)

        g2.addWidget(QLabel("To:"))
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDate(QDate(2023, 12, 31))
        self.date_to.setDisplayFormat("yyyy-MM-dd")
        g2.addWidget(self.date_to)

        g2.addStretch()
        vbox.addWidget(grp_date)

        # --- Run / Cancel ---
        run_row = QHBoxLayout()

        self.btn_run = QPushButton("▶  Calculate ET₀")
        self.btn_run.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: white; "
            "font-weight: bold; padding: 8px 20px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #388e3c; }"
        )
        self.btn_run.clicked.connect(self._run_analysis)
        run_row.addWidget(self.btn_run)

        self.btn_cancel = QPushButton("⏹  Cancel")
        self.btn_cancel.setVisible(False)
        self.btn_cancel.clicked.connect(self._cancel_analysis)
        run_row.addWidget(self.btn_cancel)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setRange(0, 0)
        run_row.addWidget(self.progress)
        run_row.addStretch()
        vbox.addLayout(run_row)

        # --- Status ---
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        vbox.addWidget(self.lbl_status)

        # --- Chart ---
        self.figure = Figure(figsize=(7, 3.2), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        vbox.addWidget(self.canvas)

        # --- Export ---
        export_row = QHBoxLayout()
        self.btn_export = QPushButton("💾  Export CSV")
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self._export_csv)
        export_row.addWidget(self.btn_export)
        export_row.addStretch()
        vbox.addLayout(export_row)

    # ------------------------------------------------------------------
    # Settings persistence
    # ------------------------------------------------------------------
    def _load_settings(self):
        s = QSettings()
        source = s.value(f"{SETTINGS_KEY}/source", SOURCE_OPEN_METEO)
        idx = self.combo_source.findText(source)
        if idx >= 0:
            self.combo_source.setCurrentIndex(idx)
        self._on_source_changed(self.combo_source.currentText())

        key = s.value(f"{SETTINGS_KEY}/api_key", "")
        if key:
            self.api_key_input.setText(key)
            self.lbl_key_status.setText("✅  Key loaded.")

    def _save_api_key(self):
        key = self.api_key_input.text().strip()
        if not key:
            QMessageBox.warning(
                self, "Missing Key", "Please paste your API key first."
            )
            return
        QSettings().setValue(f"{SETTINGS_KEY}/api_key", key)
        self.lbl_key_status.setText("✅  Key saved.")

    def _toggle_key_visibility(self):
        if self.api_key_input.echoMode() == QLineEdit.Password:
            self.api_key_input.setEchoMode(QLineEdit.Normal)
            self.btn_toggle_key.setText("Hide")
        else:
            self.api_key_input.setEchoMode(QLineEdit.Password)
            self.btn_toggle_key.setText("Show")

    def _on_source_changed(self, text):
        is_cds = (text == SOURCE_CDS)
        self.grp_cds.setVisible(is_cds)
        QSettings().setValue(f"{SETTINGS_KEY}/source", text)

    # ------------------------------------------------------------------
    # Map point picking
    # ------------------------------------------------------------------
    def _request_pick_point(self):
        self.hide()
        self.pick_point_requested.emit()

    def set_point(self, lat: float, lon: float):
        self.input_lat.setText(f"{lat:.6f}")
        self.input_lon.setText(f"{lon:.6f}")
        self._update_cell_label(lat, lon)
        if self.btn_grid_toggle.isChecked():
            self._emit_show_grid(lat, lon)
        self.show()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------------
    # Grid toggle
    # ------------------------------------------------------------------
    def _toggle_grid(self, checked: bool):
        if checked:
            self.btn_grid_toggle.setText("🔲  Hide Grid")
            try:
                lat = float(self.input_lat.text())
                lon = float(self.input_lon.text())
            except ValueError:
                QMessageBox.warning(
                    self, "No Point Selected",
                    "Pick a point on the map or enter coordinates first."
                )
                self.btn_grid_toggle.setChecked(False)
                self.btn_grid_toggle.setText("🔲  Show Grid")
                return
            self._emit_show_grid(lat, lon)
        else:
            self.btn_grid_toggle.setText("🔲  Show Grid")
            self.lbl_cell.setText("")
            self.hide_grid_requested.emit()

    def _emit_show_grid(self, lat: float, lon: float):
        resolution = self.combo_grid.currentData()
        self._update_cell_label(lat, lon)
        self.show_grid_requested.emit(lat, lon, resolution)

    def _update_cell_label(self, lat: float, lon: float):
        resolution = self.combo_grid.currentData()
        slat = snap_to_grid(lat, resolution)
        slon = snap_to_grid(lon, resolution)
        self.lbl_cell.setText(
            f"Cell centre: {slat:.2f}°, {slon:.2f}°"
        )

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------
    def _validate_inputs(self):
        source = self.combo_source.currentText()

        api_key = ""
        if source == SOURCE_CDS:
            api_key = self.api_key_input.text().strip()
            if not api_key:
                self.tabs.setCurrentWidget(self.tab_settings)
                QMessageBox.warning(
                    self, "Missing API Key",
                    "The CDS backend requires an API key.\n"
                    "Enter it in the Settings tab or switch to Open-Meteo."
                )
                return None

        try:
            lat = float(self.input_lat.text())
            lon = float(self.input_lon.text())
        except ValueError:
            QMessageBox.warning(
                self, "Invalid Coordinates",
                "Enter valid latitude and longitude values "
                "(or pick a point on the map)."
            )
            return None

        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            QMessageBox.warning(
                self, "Coordinates Out of Range",
                "Latitude must be in [−90, 90] and "
                "longitude in [−180, 180]."
            )
            return None

        d1 = self.date_from.date().toPyDate()
        d2 = self.date_to.date().toPyDate()
        if d1 > d2:
            QMessageBox.warning(
                self, "Invalid Date Range",
                "'From' date must be before 'To' date."
            )
            return None

        return source, api_key, lat, lon, d1, d2

    # ------------------------------------------------------------------
    # Run analysis (threaded)
    # ------------------------------------------------------------------
    def _run_analysis(self):
        params = self._validate_inputs()
        if params is None:
            return

        source, api_key, lat, lon, d1, d2 = params
        self._set_running(True)
        self.lbl_status.setText("⏳  Starting…")

        self._lat_for_plot = lat
        self._lon_for_plot = lon

        self._worker = _AnalysisWorker(source, api_key, lat, lon, d1, d2)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _cancel_analysis(self):
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(3000)
        self._set_running(False)
        self.lbl_status.setText("⚠️  Cancelled by user.")

    def _set_running(self, running: bool):
        self.btn_run.setEnabled(not running)
        self.btn_cancel.setVisible(running)
        self.progress.setVisible(running)
        self.btn_export.setEnabled(False)

    def _on_progress(self, msg: str):
        self.lbl_status.setText(f"⏳  {msg}")

    def _on_finished(self, daily_df):
        self._set_running(False)
        self._result_df = daily_df

        n_days = len(daily_df)
        et0_mean = daily_df["et0"].mean()
        et0_total = daily_df["et0"].sum()
        self.lbl_status.setText(
            f"✅  Done — {n_days} days  |  "
            f"Mean ET₀ = {et0_mean:.2f} mm day⁻¹  |  "
            f"Total ET₀ = {et0_total:.1f} mm"
        )

        self._plot_results(daily_df,
                           self._lat_for_plot, self._lon_for_plot)
        self.btn_export.setEnabled(True)

    def _on_error(self, msg: str):
        self._set_running(False)
        self.lbl_status.setText(f"❌  {msg}")
        QMessageBox.critical(self, "Error", msg)

    # ------------------------------------------------------------------
    # Plotting
    # ------------------------------------------------------------------
    def _plot_results(self, df: pd.DataFrame, lat: float, lon: float):
        self.figure.clear()
        ax = self.figure.add_subplot(111)

        ax.fill_between(df["date"], df["et0"], alpha=0.3, color="#2196F3")
        ax.plot(
            df["date"], df["et0"],
            linewidth=1.2, color="#1565C0", label="ET₀ (Hargreaves)"
        )
        ax.set_ylabel("ET₀  [mm day⁻¹]")
        ax.set_xlabel("Date")
        ax.set_title(
            f"Daily Potential Evapotranspiration  —  "
            f"({lat:.4f}°, {lon:.4f}°)",
            fontsize=10,
        )
        ax.legend(loc="upper right", fontsize=8)
        ax.grid(True, linestyle="--", alpha=0.5)
        self.figure.autofmt_xdate()
        self.figure.tight_layout()
        self.canvas.draw()

    # ------------------------------------------------------------------
    # CSV export
    # ------------------------------------------------------------------
    def _export_csv(self):
        if self._result_df is None:
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export ET₀ Results", "",
            "CSV Files (*.csv);;All Files (*)"
        )
        if not path:
            return

        self._result_df.to_csv(path, index=False)
        self.lbl_status.setText(f"✅  Exported to {path}")
        QMessageBox.information(
            self, "Export Complete", f"Results saved to:\n{path}"
        )
