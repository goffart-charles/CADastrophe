"""Explicit contour check, not a computation on every pointer movement."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox,QVBoxLayout,QPushButton,QLabel,QListWidget,QListWidgetItem


class ProfilePanel(QGroupBox):
    def __init__(self,session):
        super().__init__("Contours de l’esquisse")
        self.session = session
        self.report = None
        layout = QVBoxLayout(self)
        self.check = QPushButton("Vérifier l’esquisse")
        self.check.clicked.connect(session.check_profiles)
        layout.addWidget(self.check)
        self.summary = QLabel("Vérifier la fermeture et les intersections des contours.")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.results = QListWidget()
        self.results.setAccessibleName("Résultats de vérification des contours")
        self.results.setMaximumHeight(self.fontMetrics().height()*7)
        self.results.itemClicked.connect(self.highlight)
        layout.addWidget(self.results)
        self.results.hide()

    def refresh(self,sketch):
        if self.report is not None and self.report.sketch != sketch:
            self.report = None
            self.results.clear()
            self.results.hide()
            self.summary.setText("Esquisse modifiée. Vérifiez à nouveau ses contours.")

    def show_report(self,report):
        self.report = report
        self.results.clear()
        if report.valid:
            self.summary.setText(f"{len(report.contours)} contour(s) fermé(s) · {len(report.regions)} région(s) · aucun défaut.")
        else:
            self.summary.setText(f"{len(report.contours)} contour(s) fermé(s) · des défauts empêchent l’utilisation du profil.")
        for d in report.diagnostics:
            item = QListWidgetItem(d.message+(f" Écart : {self.session.surface.units.format_length(d.distance)}" if d.distance is not None else ""))
            item.setData(Qt.ItemDataRole.UserRole,d.entity_ids)
            self.results.addItem(item)
        self.results.setVisible(bool(report.diagnostics))

    def highlight(self,item):
        internal = {e.id for e in self.session.document.objects[self.session.object_id].sketch.entities}
        self.session.interaction.set_selected(set(item.data(Qt.ItemDataRole.UserRole))&internal)
