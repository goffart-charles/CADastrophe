"""Owned non-modal message in the upper-left corner of the graphics area."""
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton


class SketchNotice(QFrame):
    def __init__(self,parent,theme):
        super().__init__(parent)
        self.setObjectName("sketchNotice")
        self.setAccessibleName("Message d’esquisse")
        self.message = ""
        self.dismissed = ""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10,8,6,8)
        symbol = QLabel("!")
        symbol.setStyleSheet("font-weight: 700; font-size: 18px;")
        layout.addWidget(symbol)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.label,1)
        self.dismiss = QToolButton()
        self.dismiss.setText("×")
        self.dismiss.setAccessibleName("Fermer le message")
        self.dismiss.clicked.connect(self.close_message)
        layout.addWidget(self.dismiss,0,Qt.AlignmentFlag.AlignTop)
        self.setStyleSheet("QFrame#sketchNotice { background: %s; color: %s; border: 1px solid #b68a32; border-left: 4px solid #d5a038; border-radius: 3px; }" %
                          (("#403722","#ffe5a6") if theme == "dark" else ("#fff4ce","#493713")))
        parent.installEventFilter(self)
        self.hide()

    def close_message(self):
        self.dismissed = self.message
        self.hide()

    def set_message(self,message):
        if message != self.message:
            self.dismissed = ""
        self.message = message
        self.label.setText(message)
        if not message or message == self.dismissed:
            self.hide()
            return
        self.reposition()
        self.show()
        self.raise_()

    def reposition(self):
        self.setFixedWidth(max(120,min(420,self.parentWidget().width()-32)))
        self.adjustSize()
        self.move(16,52)

    def eventFilter(self,watched,event):
        if event.type() == QEvent.Type.Resize:
            self.reposition()
        return False
