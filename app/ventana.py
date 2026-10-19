"""Ventana principal de TAC 3D."""
import glob
import json
import os
import shutil
import subprocess
import time
import webbrowser

from PySide6.QtCore import QThread, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPixmap
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QStyle,
                               QTableWidget, QTableWidgetItem, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

import config
import motor
import requisitos
import trabajos
import validar
from config import fmt_dur

STYLE = """
* { font-family: 'Segoe UI'; font-size: 10pt; color: #dfe6f0; }
QMainWindow, QDialog, QWidget#page { background: #161b24; }
QListWidget#nav { background: #10141b; border: none; padding-top: 8px; outline: 0; }
QListWidget#nav::item { padding: 11px 14px; border-left: 3px solid transparent; }
QListWidget#nav::item:selected { background: #1f2836; border-left: 3px solid #3d8bfd; color: white; }
QLabel#h1 { font-size: 17pt; font-weight: 600; color: white; }
QLabel#h2 { font-size: 11pt; font-weight: 600; color: white; margin-top: 6px; }
QLabel#muted, QLabel[muted="true"] { color: #8b97a8; }
QFrame#card { background: #1d2430; border: 1px solid #2a3442; border-radius: 8px; }
QPushButton { background: #263042; border: 1px solid #344157; border-radius: 6px; padding: 7px 14px; }
QPushButton:hover { background: #2e3a50; }
QPushButton:disabled { color: #5c6878; background: #1d2430; }
QPushButton#primary { background: #2f6fde; border-color: #3d8bfd; color: white; font-weight: 600; }
QPushButton#primary:hover { background: #3a7ef0; }
QPushButton#primary:disabled { background: #24334d; color: #7d8ba0; }
QPushButton#danger { background: #4a2228; border-color: #6b2d36; }
QTableWidget, QTreeWidget, QPlainTextEdit, QLineEdit, QComboBox {
  background: #121720; border: 1px solid #2a3442; border-radius: 6px; selection-background-color: #2f5fb3; }
QHeaderView::section { background: #1d2430; border: none; border-bottom: 1px solid #2a3442; padding: 6px; color: #9fb0c6; }
QTableWidget::item, QTreeWidget::item { padding: 4px; }
QProgressBar { background: #121720; border: 1px solid #2a3442; border-radius: 6px; text-align: center; height: 22px; }
QProgressBar::chunk { background: #2f6fde; border-radius: 5px; }
QProgressBar#big { height: 30px; font-size: 12pt; font-weight: 600; }
QComboBox QAbstractItemView { background: #1d2430; }
QMenu { background: #1d2430; border: 1px solid #344157; }
QMenu::item:selected { background: #2f5fb3; }
QStatusBar { background: #10141b; color: #8b97a8; }
QTabWidget::pane { border: 1px solid #2a3442; }
QTabBar::tab { background: #1d2430; padding: 6px 12px; }
QTabBar::tab:selected { background: #2f5fb3; }
QScrollArea { border: none; }
"""

STATE_COLOR = {"ok": "#4cc38a", "aviso": "#f0b429", "error": "#f2555a", "info": "#6aa9ff"}


def state_icon(widget, state):
    sp = {"ok": QStyle.SP_DialogApplyButton, "aviso": QStyle.SP_MessageBoxWarning,
          "error": QStyle.SP_MessageBoxCritical, "info": QStyle.SP_MessageBoxInformation}[state]
    return widget.style().standardIcon(sp)


def open_path(p):
    if os.path.exists(p):
        os.startfile(p)


class Worker(QThread):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(object, object)

    def __init__(self, fn, *args, **kw):
        super().__init__()
        self.fn, self.args, self.kw = fn, args, kw

    def run(self):
        try:
            self.done.emit(self.fn(*self.args, **self.kw))
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}" if not isinstance(e, (validar.Cancelado, motor.Cancelado)) else "cancelado")


def page(title, subtitle=""):
    w = QWidget()
    w.setObjectName("page")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(24, 18, 24, 18)
    lay.setSpacing(10)
    h = QLabel(title)
    h.setObjectName("h1")
    lay.addWidget(h)
    if subtitle:
        s = QLabel(subtitle)
        s.setObjectName("muted")
        s.setWordWrap(True)
        lay.addWidget(s)
    return w, lay


def card():
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(16, 12, 16, 12)
    return f, lay


def check_tree(items, parent, tree=None):
    tree = tree or QTreeWidget()
    tree.clear()
    tree.setColumnCount(2)
    tree.setHeaderLabels(["Comprobacion", "Resultado"])
    tree.setRootIsDecorated(False)
    tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
    tree.header().setStretchLastSection(True)
    tree.setWordWrap(True)
    for c in items:
        it = QTreeWidgetItem([c["nombre"], c["detalle"]])
        it.setIcon(0, state_icon(parent, c["estado"]))
        it.setForeground(1, QColor(STATE_COLOR[c["estado"]]) if c["estado"] in ("error", "aviso") else QColor("#c5cfdc"))
        it.setToolTip(1, c["detalle"])
        tree.addTopLevelItem(it)
    return tree


# ------------------------------------------------------------------------------------------------ agregar
class AddDialog(QDialog):
    def __init__(self, main, path):
        super().__init__(main)
        self.main, self.path, self.result_data = main, path, None
        self.setWindowTitle("Agregar estudio")
        self.resize(860, 660)
        lay = QVBoxLayout(self)
        t = QLabel("Validando el estudio")
        t.setObjectName("h1")
        lay.addWidget(t)
        self.title = t
        p = QLabel(path)
        p.setObjectName("muted")
        p.setWordWrap(True)
        lay.addWidget(p)
        self.bar = QProgressBar()
        lay.addWidget(self.bar)
        self.tree = check_tree([], self)
        lay.addWidget(self.tree, 1)

        self.form_box, fl = card()
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        self.l_pac, self.l_est = QLabel("-"), QLabel("-")
        self.c_serie = QComboBox()
        self.c_serie.currentIndexChanged.connect(self.serie_changed)
        self.e_id = QLineEdit()
        self.l_time = QLabel("-")
        form.addRow("Paciente:", self.l_pac)
        form.addRow("Estudio:", self.l_est)
        form.addRow("Serie a reconstruir:", self.c_serie)
        form.addRow("Nombre en la biblioteca:", self.e_id)
        form.addRow("Tiempo estimado:", self.l_time)
        fl.addLayout(form)
        self.form_box.setEnabled(False)
        lay.addWidget(self.form_box)

        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg)
        bb = QHBoxLayout()
        bb.addStretch()
        self.b_cancel = QPushButton("Cancelar")
        self.b_cancel.clicked.connect(self.reject)
        self.b_ok = QPushButton("Agregar y procesar")
        self.b_ok.setObjectName("primary")
        self.b_ok.setEnabled(False)
        self.b_ok.clicked.connect(self.accept_job)
        bb.addWidget(self.b_cancel)
        bb.addWidget(self.b_ok)
        lay.addLayout(bb)

        self._cancel = False
        self.w = Worker(validar.validate, path, main.cfg.library,
                        progress=lambda a, b: self.w.progress.emit(a, b), cancelled=lambda: self._cancel)
        self.w.progress.connect(lambda a, b: (self.bar.setMaximum(max(1, b)), self.bar.setValue(a),
                                              self.bar.setFormat(f"Leyendo cabeceras DICOM: {a} de {b} archivos")))
        self.w.done.connect(self.validated)
        self.w.failed.connect(lambda e: self.msg.setText(f"<span style='color:#f2555a'>Error al validar: {e}</span>"))
        self.w.start()

    def reject(self):
        self._cancel = True
        self.w.wait(3000)
        super().reject()

    def validated(self, r):
        self.result_data = r
        self.bar.setMaximum(1)
        self.bar.setValue(1)
        ok = r["ok"]
        n_av = sum(c["estado"] == "aviso" for c in r["checks"])
        self.title.setText("Estudio valido" if ok else "El estudio no se puede procesar")
        self.bar.setFormat("Validacion completa" + (f" - {n_av} avisos" if n_av and ok else ""))
        check_tree(r["checks"], self, self.tree)
        if not r.get("series") or not r.get("elegida"):
            self.msg.setText("<span style='color:#f2555a'>Corrige los errores marcados en rojo y vuelve a intentarlo.</span>")
            return
        i = r["info"]
        self.l_pac.setText(f"<b>{i['paciente'] or '(sin nombre)'}</b>  {i['sexo']} {i['edad'] + ' anos' if i['edad'] else ''}")
        self.l_est.setText(f"{i['estudio'] or '-'}  ({i['fecha']})  {i['institucion']}")
        self.c_serie.blockSignals(True)
        for s in r["series"]:
            label = f"#{s['numero']} {s['descripcion']} - {s['n']} cortes, {s['grosor']:g} mm"
            self.c_serie.addItem(label + ("" if s["util"] else "  (no utilizable)"), s["uid"])
            if not s["util"]:
                self.c_serie.model().item(self.c_serie.count() - 1).setEnabled(False)
        self.c_serie.setCurrentIndex(self.c_serie.findData(r["elegida"]))
        self.c_serie.blockSignals(False)
        self.e_id.setText(r.get("duplicado") or r["id"])
        self.form_box.setEnabled(True)
        self.update_time(r["voxeles"])
        self.b_ok.setEnabled(ok)
        if not ok:
            self.msg.setText("<span style='color:#f2555a'>Hay errores que impiden procesar este estudio (en rojo).</span>")
        elif r.get("duplicado"):
            self.msg.setText("<span style='color:#f0b429'>Este estudio ya esta en la biblioteca: se volvera a procesar "
                             "y se conservaran los hallazgos anotados.</span>")

    def update_time(self, vox):
        dev = self.main.runner.device()
        t = trabajos.estimate("nuevo", vox, dev)
        q = sum(j.progress()[1] for j in self.main.runner.jobs if j.estado in ("cola", "procesando"))
        txt = f"~{fmt_dur(t)} con {'la GPU' if dev == 'gpu' else 'el procesador (sin GPU NVIDIA)'}"
        if q > 0:
            txt += f"  (+ {fmt_dur(q)} de estudios en cola antes que este)"
        self.l_time.setText(txt)

    def serie_changed(self, idx):
        r = self.result_data
        uid = self.c_serie.itemData(idx)
        if not r or not uid:
            return
        names = {c["nombre"] for c in validar.check_series(r["_series"][r["elegida"]])}
        checks, vox = validar.series_report(r, uid)
        r["checks"] = checks + [c for c in r["checks"] if c["nombre"] not in names]
        r["voxeles"] = vox
        check_tree(r["checks"], self, self.tree)
        ok = not any(c["estado"] == "error" for c in r["checks"])
        self.b_ok.setEnabled(ok)
        self.update_time(vox)

    def accept_job(self):
        r = self.result_data
        sid = validar.study_id(self.e_id.text().strip() or r["id"])
        uid = self.c_serie.currentData()
        dup = os.path.isdir(os.path.join(self.main.cfg.library, sid))
        if dup and sid != r.get("duplicado"):
            if QMessageBox.question(self, "Nombre en uso", f"Ya hay un estudio llamado '{sid}'. ¿Reemplazarlo?") != QMessageBox.Yes:
                return
        job = trabajos.Job("nuevo", sid, entrada=self.path, serie=uid if uid != r["elegida"] else None,
                           voxeles=r["voxeles"], nombre=r["info"].get("paciente") or sid, reemplazar=dup)
        if not self.main.runner.add(job):
            QMessageBox.information(self, "En proceso", "Ese estudio ya esta en la cola.")
            return
        self.accept()


# ------------------------------------------------------------------------------------------------ estudios
class StudiesPage(QWidget):
    COLS = ["Paciente", "Estudio", "Fecha", "Estado", "Estructuras", "Procesado", "Carpeta"]

    def __init__(self, main):
        super().__init__()
        self.main = main
        w, lay = page("Estudios", "Biblioteca de tomografias reconstruidas. Agrega un estudio en .zip o una carpeta "
                                  "con los DICOM: se valida antes de procesarlo.")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(w)

        bar = QHBoxLayout()
        add = QPushButton("+  Agregar estudio")
        add.setObjectName("primary")
        m = QMenu(add)
        m.addAction("Desde un archivo .zip...", self.add_zip)
        m.addAction("Desde una carpeta con DICOM...", self.add_folder)
        add.setMenu(m)
        bar.addWidget(add)
        self.b_view = QPushButton("Ver en 3D")
        self.b_view.clicked.connect(lambda: self.act("ver"))
        bar.addWidget(self.b_view)
        self.b_more = QPushButton("Acciones")
        self.menu = QMenu(self.b_more)
        for key, label in (("modelos", "Regenerar modelos 3D (rapido)"), ("completo", "Repetir todo con IA"),
                           (None, None), ("hallazgos", "Editar hallazgos del informe..."), ("informe", "Abrir informe PDF"),
                           ("control", "Imagenes de control"), ("stl", "Abrir modelos STL (impresion 3D)"),
                           ("carpeta", "Abrir carpeta del estudio"), ("registro", "Ver ultimo registro"),
                           (None, None), ("eliminar", "Eliminar estudio...")):
            if key is None:
                self.menu.addSeparator()
            else:
                self.menu.addAction(label, lambda k=key: self.act(k))
        self.b_more.setMenu(self.menu)
        bar.addWidget(self.b_more)
        bar.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar paciente o estudio")
        self.search.setFixedWidth(260)
        self.search.textChanged.connect(self.refresh)
        bar.addWidget(self.search)
        lay.addLayout(bar)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.doubleClicked.connect(lambda _: self.act("ver"))
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(lambda p: self.menu.exec(self.table.viewport().mapToGlobal(p)))
        self.table.itemSelectionChanged.connect(self.sel_changed)
        lay.addWidget(self.table, 1)
        self.empty = QLabel("No hay estudios todavia. Pulsa 'Agregar estudio' o arrastra un .zip a esta ventana.")
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.empty)
        self.refresh()

    def studies(self):
        lib = self.main.cfg.library
        out = []
        for sid in sorted(os.listdir(lib)) if os.path.isdir(lib) else []:
            sdir = os.path.join(lib, sid)
            if not os.path.isdir(sdir) or sid.startswith("."):
                continue
            info, man = {}, {}
            try:
                info = json.load(open(os.path.join(sdir, "estudio.json"), encoding="utf-8"))
            except Exception:
                pass
            mf = os.path.join(sdir, "web", "manifest.json")
            try:
                man = json.load(open(mf, encoding="utf-8"))
            except Exception:
                pass
            if not info and not man and not self.main.runner.job_for(sid):
                continue
            out.append({"id": sid, "dir": sdir, "info": info, "n": len(man.get("structures", [])),
                        "generado": man.get("generado"), "listo": bool(man)})
        return out

    def refresh(self):
        q = self.search.text().strip().lower()
        sel = self.selected()
        items = [s for s in self.studies() if not q or q in (s["id"] + json.dumps(s["info"], ensure_ascii=False)).lower()]
        self.table.setRowCount(len(items))
        for r, s in enumerate(items):
            i = s["info"]
            job = self.main.runner.job_for(s["id"])
            if job and job.estado == "procesando":
                st, col = f"Procesando {job.progress()[0]:.0f}%", "#6aa9ff"
            elif job:
                st, col = "En cola", "#6aa9ff"
            elif s["listo"]:
                st, col = "Listo", "#4cc38a"
            else:
                st, col = "Incompleto", "#f0b429"
            vals = [i.get("paciente") or s["id"], i.get("estudio") or "-", i.get("fecha", "-"), st,
                    str(s["n"] or "-"), time.strftime("%d/%m/%Y %H:%M", time.localtime(s["generado"])) if s["generado"] else "-",
                    s["id"]]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, s["id"])
                if c == 3:
                    it.setForeground(QColor(col))
                self.table.setItem(r, c, it)
            if s["id"] == sel:
                self.table.selectRow(r)
        self.empty.setVisible(not items)
        self.sel_changed()

    def selected(self):
        r = self.table.currentRow()
        it = self.table.item(r, 0) if r >= 0 else None
        return it.data(Qt.UserRole) if it else None

    def sel_changed(self):
        on = self.selected() is not None
        self.b_view.setEnabled(on)
        self.b_more.setEnabled(on)

    def add_zip(self):
        p, _ = QFileDialog.getOpenFileName(self, "Elige el estudio", os.path.expanduser("~/Downloads"),
                                           "Estudios DICOM (*.zip);;Todos los archivos (*)")
        if p:
            self.main.add_study(p)

    def add_folder(self):
        p = QFileDialog.getExistingDirectory(self, "Carpeta con las imagenes DICOM", os.path.expanduser("~"))
        if p:
            self.main.add_study(p)

    def act(self, key):
        sid = self.selected()
        if not sid:
            return
        lib = self.main.cfg.library
        sdir = os.path.join(lib, sid)
        if key == "ver":
            if not os.path.exists(os.path.join(sdir, "web", "manifest.json")):
                QMessageBox.information(self, "Sin modelo", "Este estudio todavia no tiene modelo 3D. Procesalo primero.")
                return
            self.main.open_viewer(sid)
        elif key in ("modelos", "completo"):
            if not os.path.exists(os.path.join(sdir, "trabajo", "ct.nii.gz")):
                QMessageBox.warning(self, "No se puede", "Faltan los datos del estudio: vuelve a agregarlo desde el .zip.")
                return
            if key == "completo" and QMessageBox.question(
                    self, "Repetir con IA", "Se repetiran todas las segmentaciones con IA. "
                    f"Tardara ~{fmt_dur(trabajos.estimate('completo', self.voxels(sdir), self.main.runner.device()))}. ¿Continuar?") != QMessageBox.Yes:
                return
            if not self.main.runner.add(trabajos.Job(key, sid, voxeles=self.voxels(sdir), nombre=sid)):
                QMessageBox.information(self, "En proceso", "Ese estudio ya esta en la cola.")
            else:
                self.main.go("cola")
        elif key == "hallazgos":
            p = os.path.join(sdir, "hallazgos.json")
            if os.path.exists(p):
                subprocess.Popen(["notepad.exe", p])
                QMessageBox.information(self, "Hallazgos", "Edita y guarda el archivo. Despues usa 'Acciones > Regenerar "
                                        "modelos 3D' para verlos en el visor.")
        elif key == "informe":
            pdfs = sorted(glob.glob(os.path.join(sdir, "informe*.pdf")))
            if pdfs:
                open_path(pdfs[0])
            else:
                QMessageBox.information(self, "Informe", "Este estudio no traia informe en PDF.")
        elif key == "control":
            ImagesDialog(self, sid, os.path.join(sdir, "control")).exec()
        elif key == "stl":
            open_path(os.path.join(sdir, "stl"))
        elif key == "carpeta":
            open_path(sdir)
        elif key == "registro":
            logs = sorted(glob.glob(os.path.join(config.LOGS, f"*_{sid}.log")))
            if logs:
                subprocess.Popen(["notepad.exe", logs[-1]])
            else:
                QMessageBox.information(self, "Registro", "No hay registros de este estudio en este equipo.")
        elif key == "eliminar":
            if self.main.runner.job_for(sid):
                QMessageBox.warning(self, "En proceso", "Cancela primero su procesamiento.")
                return
            if QMessageBox.warning(self, "Eliminar", f"¿Eliminar definitivamente el estudio '{sid}' y todos sus archivos?",
                                   QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
                shutil.rmtree(sdir, ignore_errors=True)
                trabajos.update_index(lib)
                self.refresh()

    @staticmethod
    def voxels(sdir):
        try:
            e = json.load(open(os.path.join(sdir, "estudio.json"), encoding="utf-8"))
            return 512 * 512 * int(e.get("cortes") or 150)
        except Exception:
            return 40e6


class ImagesDialog(QDialog):
    def __init__(self, parent, sid, folder):
        super().__init__(parent)
        self.setWindowTitle(f"Imagenes de control - {sid}")
        self.resize(1100, 800)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        names = {"control_axial.png": "Axial", "control_coronal.png": "Coronal", "hallazgos.png": "Hallazgos",
                 "candidatos_calculos.png": "Candidatos a calculo"}
        for p in sorted(glob.glob(os.path.join(folder, "*.png"))):
            sc = QScrollArea()
            lb = QLabel()
            pm = QPixmap(p)
            lb.setPixmap(pm.scaledToWidth(min(pm.width(), 1050), Qt.SmoothTransformation))
            sc.setWidget(lb)
            tabs.addTab(sc, names.get(os.path.basename(p), os.path.basename(p)))
        if not tabs.count():
            lay.addWidget(QLabel("No hay imagenes de control."))
        lay.addWidget(tabs)


# ------------------------------------------------------------------------------------------------ cola
class QueuePage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        w, lay = page("Procesamiento", "Progreso de los estudios en proceso y en cola. Los tiempos estimados se ajustan "
                                       "a la velocidad real de este equipo con cada estudio procesado.")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(w)

        self.box, bl = card()
        top = QHBoxLayout()
        self.l_name = QLabel("Sin procesos en curso")
        self.l_name.setObjectName("h2")
        top.addWidget(self.l_name)
        top.addStretch()
        self.b_cancel = QPushButton("Cancelar")
        self.b_cancel.setObjectName("danger")
        self.b_cancel.clicked.connect(self.cancel)
        top.addWidget(self.b_cancel)
        bl.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setObjectName("big")
        self.bar.setRange(0, 1000)
        bl.addWidget(self.bar)
        g = QGridLayout()
        self.l_step, self.l_el, self.l_left, self.l_end = QLabel("-"), QLabel("-"), QLabel("-"), QLabel("-")
        for i, (t, l) in enumerate((("Etapa actual", self.l_step), ("Transcurrido", self.l_el),
                                    ("Tiempo restante", self.l_left), ("Terminara a las", self.l_end))):
            a = QLabel(t)
            a.setObjectName("muted")
            g.addWidget(a, 0, i)
            l.setStyleSheet("font-size: 12pt; font-weight: 600;")
            g.addWidget(l, 1, i)
        bl.addLayout(g)
        self.l_live = QLabel("")
        self.l_live.setObjectName("muted")
        self.l_live.setStyleSheet("font-family: Consolas; font-size: 9pt;")
        bl.addWidget(self.l_live)
        lay.addWidget(self.box)

        mid = QHBoxLayout()
        left = QVBoxLayout()
        h = QLabel("Etapas")
        h.setObjectName("h2")
        left.addWidget(h)
        self.steps = QTreeWidget()
        self.steps.setHeaderLabels(["Etapa", "Estado", "Tiempo"])
        self.steps.setRootIsDecorated(False)
        self.steps.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.steps.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.steps.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        left.addWidget(self.steps)
        mid.addLayout(left, 3)
        right = QVBoxLayout()
        h = QLabel("Cola")
        h.setObjectName("h2")
        right.addWidget(h)
        self.queue = QTreeWidget()
        self.queue.setHeaderLabels(["Estudio", "Tarea", "Estado", "Duracion"])
        self.queue.setRootIsDecorated(False)
        self.queue.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.queue.setContextMenuPolicy(Qt.CustomContextMenu)
        self.queue.customContextMenuRequested.connect(self.queue_menu)
        right.addWidget(self.queue)
        mid.addLayout(right, 2)
        lay.addLayout(mid, 2)

        lh = QHBoxLayout()
        h = QLabel("Registro")
        h.setObjectName("h2")
        lh.addWidget(h)
        lh.addStretch()
        b = QPushButton("Abrir registro completo")
        b.clicked.connect(self.open_log)
        lh.addWidget(b)
        lay.addLayout(lh)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        self.log.setStyleSheet("font-family: Consolas; font-size: 9pt;")
        lay.addWidget(self.log, 2)
        self.shown = None
        self.refresh()

    def job(self):
        r = self.main.runner
        return r.current or (r.jobs[-1] if r.jobs else None)

    def on_output(self, job, line):
        if job is self.shown:
            self.log.appendPlainText(line)

    def refresh(self):
        j = self.job()
        if j is not self.shown:
            self.shown = j
            self.log.clear()
            if j and j.log_path and os.path.exists(j.log_path):
                self.log.setPlainText(open(j.log_path, encoding="utf-8", errors="replace").read()[-60000:])
        self.b_cancel.setEnabled(bool(j and j.estado in ("procesando", "cola")))
        if not j:
            self.l_name.setText("Sin procesos en curso")
            self.bar.setValue(0)
            self.bar.setFormat("")
            for l in (self.l_step, self.l_el, self.l_left, self.l_end):
                l.setText("-")
        else:
            pct, left, frac = j.progress()
            self.l_name.setText(f"{trabajos.KIND_LABEL[j.kind]}: {j.nombre}")
            self.bar.setValue(int(pct * 10))
            if j.estado == "procesando":
                self.bar.setFormat(f"{pct:.0f} %")
                self.l_step.setText(trabajos.STEP_LABELS.get(j.etapa, "Preparando..."))
                el = time.time() - j.inicio
                self.l_el.setText(fmt_dur(el))
                self.l_left.setText("~" + fmt_dur(left))
                self.l_end.setText(time.strftime("%H:%M", time.localtime(time.time() + left)))
                self.l_live.setText(j.ultima[-140:])
            else:
                fin = {"listo": "Terminado", "error": "Error", "cancelado": "Cancelado", "cola": "En cola"}[j.estado]
                self.bar.setFormat(fin if j.estado != "listo" else "100 % - Terminado")
                self.l_step.setText(fin)
                self.l_el.setText(fmt_dur((j.fin or time.time()) - j.inicio) if j.inicio else "-")
                self.l_left.setText("-" if j.estado != "cola" else "~" + fmt_dur(left))
                self.l_end.setText(time.strftime("%H:%M", time.localtime(j.fin)) if j.fin else "-")
                self.l_live.setText(("<span style='color:#f2555a'>" + j.error + "</span>") if j.error else "")
            self.fill_steps(j)
        self.queue.clear()
        for q in reversed(self.main.runner.jobs):
            pct = q.progress()[0]
            st = {"cola": "En cola", "procesando": f"{pct:.0f} %", "listo": "Listo", "error": "Error",
                  "cancelado": "Cancelado"}[q.estado]
            dur = fmt_dur((q.fin or time.time()) - q.inicio) if q.inicio else "~" + fmt_dur(q.progress()[1])
            it = QTreeWidgetItem([q.nombre, trabajos.KIND_LABEL[q.kind], st, dur])
            it.setForeground(2, QColor({"listo": "#4cc38a", "error": "#f2555a", "procesando": "#6aa9ff"}.get(q.estado, "#c5cfdc")))
            it.setData(0, Qt.UserRole, q.num)
            self.queue.addTopLevelItem(it)

    def fill_steps(self, j):
        self.steps.clear()
        pct, left, frac = j.progress()
        for s in j.plan:
            if s in j.durs:
                st, t, col = "Hecho", fmt_dur(j.durs[s]), "#4cc38a"
                if s == j.etapa and j.estado in ("error", "cancelado"):
                    st, col = ("Fallo" if j.estado == "error" else "Cancelado"), "#f2555a"
            elif s == j.etapa and j.estado == "procesando":
                st, t, col = f"En curso {frac * 100:.0f}%", f"{fmt_dur(time.time() - j.t_etapa)} de ~{fmt_dur(j.exp(s))}", "#6aa9ff"
            else:
                st, t, col = "Pendiente", "~" + fmt_dur(j.exp(s)), "#8b97a8"
            it = QTreeWidgetItem([trabajos.STEP_LABELS.get(s, s), st, t])
            it.setForeground(1, QColor(col))
            self.steps.addTopLevelItem(it)

    def cancel(self):
        j = self.job()
        if j and QMessageBox.question(self, "Cancelar", f"¿Cancelar el procesamiento de {j.nombre}?") == QMessageBox.Yes:
            self.main.runner.cancel(j)

    def queue_menu(self, pos):
        it = self.queue.itemAt(pos)
        if not it:
            return
        j = next((q for q in self.main.runner.jobs if q.num == it.data(0, Qt.UserRole)), None)
        m = QMenu(self)
        if j and j.estado == "cola":
            m.addAction("Quitar de la cola", lambda: self.main.runner.remove(j))
        if j and j.log_path:
            m.addAction("Abrir registro", lambda: subprocess.Popen(["notepad.exe", j.log_path]))
        if j and j.estado == "listo":
            m.addAction("Ver en 3D", lambda: self.main.open_viewer(j.sid))
        if not m.isEmpty():
            m.exec(self.queue.viewport().mapToGlobal(pos))

    def open_log(self):
        j = self.job()
        if j and j.log_path and os.path.exists(j.log_path):
            subprocess.Popen(["notepad.exe", j.log_path])
        else:
            open_path(config.LOGS)


# ------------------------------------------------------------------------------------------------ visor
class ViewerPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(10, 6, 10, 6)
        for label, fn in (("Recargar", lambda: self.view.reload()), ("Abrir en el navegador", self.browser),
                          ("Pantalla completa (F11)", main.toggle_full)):
            b = QPushButton(label)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch()
        lay.addLayout(bar)
        self.view = QWebEngineView()
        s = self.view.settings()
        s.setAttribute(QWebEngineSettings.WebGLEnabled, True)
        s.setAttribute(QWebEngineSettings.Accelerated2dCanvasEnabled, True)
        self.view.page().setBackgroundColor(QColor("#1b2230"))
        lay.addWidget(self.view, 1)
        self.sid = None

    def show_study(self, sid=None):
        self.sid = sid
        self.view.setUrl(QUrl(self.main.server.url(sid)))

    def ensure(self):
        if self.view.url().isEmpty():
            self.show_study(None)

    def browser(self):
        webbrowser.open(self.main.server.url(self.sid))


# ------------------------------------------------------------------------------------------------ equipo
class SystemPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        w, lay = page("Equipo y motor de IA", "Requisitos minimos para procesar estudios y estado del motor de "
                                              "inteligencia artificial (TotalSegmentator).")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(w)
        self.l_sum = QLabel("Comprobando...")
        self.l_sum.setObjectName("h2")
        lay.addWidget(self.l_sum)
        self.tree = check_tree([], self)
        lay.addWidget(self.tree, 2)
        b = QHBoxLayout()
        self.b_check = QPushButton("Volver a comprobar")
        self.b_check.clicked.connect(self.check)
        b.addWidget(self.b_check)
        b.addStretch()
        lay.addLayout(b)

        self.mbox, ml = card()
        h = QLabel("Motor de IA")
        h.setObjectName("h2")
        ml.addWidget(h)
        self.l_motor = QLabel("-")
        self.l_motor.setWordWrap(True)
        ml.addWidget(self.l_motor)
        row = QHBoxLayout()
        row.addWidget(QLabel("Instalar para:"))
        self.c_dev = QComboBox()
        self.c_dev.addItem("Tarjeta grafica NVIDIA (rapido, ~6 GB)", "gpu")
        self.c_dev.addItem("Solo procesador (lento, ~2.5 GB)", "cpu")
        row.addWidget(self.c_dev)
        row.addStretch()
        self.b_inst = QPushButton("Instalar motor de IA")
        self.b_inst.setObjectName("primary")
        self.b_inst.clicked.connect(self.install)
        row.addWidget(self.b_inst)
        self.b_icancel = QPushButton("Cancelar instalacion")
        self.b_icancel.clicked.connect(self.cancel_install)
        self.b_icancel.hide()
        row.addWidget(self.b_icancel)
        ml.addLayout(row)
        self.ibar = QProgressBar()
        self.ibar.setRange(0, 1000)
        self.ibar.hide()
        ml.addWidget(self.ibar)
        self.ilog = QPlainTextEdit()
        self.ilog.setReadOnly(True)
        self.ilog.setMaximumBlockCount(2000)
        self.ilog.setStyleSheet("font-family: Consolas; font-size: 9pt;")
        self.ilog.setMaximumHeight(160)
        self.ilog.hide()
        ml.addWidget(self.ilog)
        lay.addWidget(self.mbox)
        self.installer = None
        self.state = None
        self.worker = None

    def check(self):
        if self.worker and self.worker.isRunning():
            return
        self.b_check.setEnabled(False)
        self.l_sum.setText("Comprobando el equipo...")
        self.worker = Worker(requisitos.check_all, self.main.cfg)
        self.worker.done.connect(self.checked)
        self.worker.failed.connect(lambda e: (self.l_sum.setText("Error al comprobar: " + e), self.b_check.setEnabled(True)))
        self.worker.start()

    def checked(self, r):
        res, m, g = r
        self.state = r
        self.b_check.setEnabled(True)
        check_tree(res, self, self.tree)
        s = requisitos.summary(res)
        self.l_sum.setText({"ok": "El equipo cumple todos los requisitos",
                            "aviso": "El equipo puede procesar estudios, revisa los avisos",
                            "error": "Hay requisitos sin cumplir (en rojo)"}[s])
        self.l_sum.setStyleSheet(f"color: {STATE_COLOR[s]}")
        gpu_ok = requisitos.gpu_usable(g)
        self.c_dev.setCurrentIndex(0 if gpu_ok else 1)
        self.c_dev.model().item(0).setEnabled(gpu_ok)
        miss = requisitos.models_missing()
        if m["ok"]:
            self.l_motor.setText(f"Instalado: {m['version']}. Usa {'la GPU' if m['cuda'] else 'el procesador'}.<br>"
                                 f"<span style='color:#8b97a8'>{m['python']}</span>"
                                 + ("<br>Faltan modelos: se descargaran al instalar." if miss else ""))
            self.b_inst.setText("Descargar modelos que faltan" if miss else "Reinstalar motor")
        else:
            self.l_motor.setText("No instalado. La instalacion descarga Python, PyTorch, TotalSegmentator y los modelos "
                                 "de IA (necesita internet; tarda de 10 a 40 minutos segun la conexion). Solo se hace una vez.")
            self.b_inst.setText("Instalar motor de IA")
        self.main.motor_changed(m)

    def install(self):
        if self.installer:
            return
        m = self.state[1] if self.state else {"ok": False}
        dev = self.c_dev.currentData()
        if m.get("ok") and not requisitos.models_missing():
            if QMessageBox.question(self, "Reinstalar", "El motor ya esta instalado. ¿Reinstalarlo desde cero?") != QMessageBox.Yes:
                return
        if self.main.runner.busy():
            QMessageBox.warning(self, "Ocupado", "Espera a que termine el procesamiento en curso.")
            return
        only_models = m.get("ok") and requisitos.models_missing()
        self.ibar.show()
        self.ilog.show()
        self.ilog.clear()
        self.b_inst.setEnabled(False)
        self.b_icancel.show()
        inst = motor.Installer(dev, on_progress=lambda f, t: self.iw.progress.emit(f, t),
                               on_log=lambda l: self.iw.progress.emit(None, l))
        if only_models:
            inst.python = m["python"]
            inst.steps = [s for s in inst.steps if s[0] in ("modelos", "verificar")]
        self.installer = inst
        self.t0 = time.time()
        self.iw = Worker(inst.run)
        self.iw.progress.connect(self.iprogress)
        self.iw.done.connect(self.installed)
        self.iw.failed.connect(self.ifailed)
        self.iw.start()

    def iprogress(self, f, t):
        if f is None:
            self.ilog.appendPlainText(t)
            return
        self.ibar.setValue(int(f * 1000))
        el = time.time() - self.t0
        eta = f" - quedan ~{fmt_dur(el / f - el)}" if f > 0.05 else ""
        self.ibar.setFormat(f"{f * 100:.0f}% - {t}{eta}")

    def installed(self, py):
        self.installer = None
        if py and os.path.exists(py) and not py.startswith(os.path.join(config.LOCAL, "tac3d_env")):
            self.main.cfg["motor_python"] = py
            self.main.cfg.save()
        self.b_inst.setEnabled(True)
        self.b_icancel.hide()
        self.ibar.setFormat("Motor de IA instalado")
        QMessageBox.information(self, "Listo", "El motor de IA quedo instalado. Ya puedes procesar estudios.")
        self.check()

    def ifailed(self, e):
        self.installer = None
        self.b_inst.setEnabled(True)
        self.b_icancel.hide()
        self.ibar.setFormat("Instalacion cancelada" if e == "cancelado" else "La instalacion fallo")
        if e != "cancelado":
            self.ilog.appendPlainText("\nERROR: " + e)
            QMessageBox.critical(self, "Error", "La instalacion fallo:\n" + e + "\n\nRevisa la conexion a internet y vuelve a intentarlo.")
        self.check()

    def cancel_install(self):
        if self.installer:
            self.installer.cancel()


# ------------------------------------------------------------------------------------------------ ajustes
class SettingsPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        cfg = main.cfg
        w, lay = page("Ajustes")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(w)
        box, bl = card()
        form = QFormLayout()
        row = QHBoxLayout()
        self.e_lib = QLineEdit(cfg.library)
        self.e_lib.setReadOnly(True)
        row.addWidget(self.e_lib)
        b = QPushButton("Cambiar...")
        b.clicked.connect(self.change_lib)
        row.addWidget(b)
        b = QPushButton("Abrir")
        b.clicked.connect(lambda: open_path(cfg.library))
        row.addWidget(b)
        form.addRow("Carpeta de estudios:", row)
        self.c_dev = QComboBox()
        for label, v in (("Automatico (GPU si hay una compatible)", "auto"), ("Forzar GPU NVIDIA", "gpu"),
                         ("Forzar procesador", "cpu")):
            self.c_dev.addItem(label, v)
        self.c_dev.setCurrentIndex(max(0, self.c_dev.findData(cfg.get("dispositivo", "auto"))))
        self.c_dev.currentIndexChanged.connect(self.save)
        form.addRow("Procesar con:", self.c_dev)
        self.k_tmp = QCheckBox("Borrar los archivos temporales al terminar cada estudio")
        self.k_tmp.setChecked(cfg.get("borrar_temporales", True))
        self.k_tmp.toggled.connect(self.save)
        form.addRow("", self.k_tmp)
        bl.addLayout(form)
        r2 = QHBoxLayout()
        b = QPushButton("Abrir carpeta de registros")
        b.clicked.connect(lambda: open_path(config.LOGS))
        r2.addWidget(b)
        b = QPushButton("Reiniciar tiempos estimados")
        b.clicked.connect(self.reset_times)
        r2.addWidget(b)
        r2.addStretch()
        bl.addLayout(r2)
        lay.addWidget(box)

        box, bl = card()
        h = QLabel(f"{config.APP_NAME} {config.VERSION}")
        h.setObjectName("h2")
        bl.addWidget(h)
        t = QLabel("Reconstruccion 3D de tomografias con IA (TotalSegmentator, nnU-Net) y visor interactivo.<br><br>"
                   "<b>Aviso:</b> los modelos se generan automaticamente y pueden contener errores. Son una ayuda para "
                   "entender el estudio y no reemplazan el informe del radiologo ni sirven para diagnostico.<br><br>"
                   f"<span style='color:#8b97a8'>Datos de la app: {config.DATA}<br>Modelos de IA: {config.MODELOS_DIR}</span>")
        t.setWordWrap(True)
        t.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bl.addWidget(t)
        lay.addWidget(box)
        lay.addStretch()

    def save(self):
        self.main.cfg["dispositivo"] = self.c_dev.currentData()
        self.main.cfg["borrar_temporales"] = self.k_tmp.isChecked()
        self.main.cfg.save()

    def change_lib(self):
        if self.main.runner.busy():
            QMessageBox.warning(self, "Ocupado", "Espera a que termine el procesamiento en curso.")
            return
        p = QFileDialog.getExistingDirectory(self, "Carpeta de estudios", self.main.cfg.library)
        if p:
            self.main.cfg["estudios"] = os.path.normpath(p)
            self.main.cfg.save()
            self.e_lib.setText(self.main.cfg.library)
            self.main.studies.refresh()
            self.main.viewer.view.setUrl(QUrl())

    def reset_times(self):
        if os.path.exists(config.TIEMPOS_FILE):
            os.remove(config.TIEMPOS_FILE)
        QMessageBox.information(self, "Tiempos", "Se usaran de nuevo los tiempos de referencia.")


# ------------------------------------------------------------------------------------------------ principal
class MainWindow(QMainWindow):
    PAGES = (("estudios", "Estudios"), ("cola", "Procesamiento"), ("visor", "Visor 3D"), ("equipo", "Equipo y motor"),
             ("ajustes", "Ajustes"))

    def __init__(self, cfg, server, runner):
        super().__init__()
        self.cfg, self.server, self.runner = cfg, server, runner
        self.setWindowTitle(config.APP_NAME)
        ico = os.path.join(config.APP_RES, "tac3d.ico")
        if os.path.exists(ico):
            self.setWindowIcon(QIcon(ico))
        self.resize(1400, 880)
        self.setStyleSheet(STYLE)
        self.setAcceptDrops(True)

        root = QWidget()
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        side = QWidget()
        side.setStyleSheet("background: #10141b;")
        side.setFixedWidth(200)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 14, 0, 10)
        logo = QLabel(f"<span style='font-size:16pt;font-weight:700;color:white'>TAC 3D</span><br>"
                      f"<span style='color:#8b97a8;font-size:8pt'>reconstruccion con IA</span>")
        logo.setContentsMargins(16, 0, 0, 8)
        sl.addWidget(logo)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        sl.addWidget(self.nav, 1)
        self.l_motor = QLabel("Motor: comprobando...")
        self.l_motor.setObjectName("muted")
        self.l_motor.setWordWrap(True)
        self.l_motor.setContentsMargins(14, 0, 8, 0)
        sl.addWidget(self.l_motor)
        h.addWidget(side)

        self.stack = QStackedWidget()
        self.studies = StudiesPage(self)
        self.queue = QueuePage(self)
        self.viewer = ViewerPage(self)
        self.system = SystemPage(self)
        self.settings_page = SettingsPage(self)
        self.page_map = {}
        for (key, label), w in zip(self.PAGES, (self.studies, self.queue, self.viewer, self.system, self.settings_page)):
            self.page_map[key] = self.stack.addWidget(w)
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, key)
            self.nav.addItem(it)
        self.nav.currentRowChanged.connect(self.on_nav)
        h.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.statusBar().showMessage("Listo")
        self.l_status = QLabel("")
        self.statusBar().addPermanentWidget(self.l_status)

        runner.changed.connect(lambda j: self.refresh_soon())
        runner.output.connect(self.queue.on_output)
        runner.finished.connect(self.job_finished)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        self._dirty = False
        self.nav.setCurrentRow(0)
        fs = QAction(self)
        fs.setShortcut("F11")
        fs.triggered.connect(self.toggle_full)
        self.addAction(fs)
        QTimer.singleShot(200, self.system.check)

    def go(self, key):
        self.nav.setCurrentRow(list(self.page_map).index(key))

    def on_nav(self, row):
        key = self.nav.item(row).data(Qt.UserRole)
        self.stack.setCurrentIndex(self.page_map[key])
        if key == "visor":
            self.viewer.ensure()
        if key == "estudios":
            self.studies.refresh()

    def refresh_soon(self):
        self._dirty = True

    def tick(self):
        j = self.runner.current
        if j:
            pct, left, _ = j.progress()
            self.l_status.setText(f"Procesando {j.nombre}: {pct:.0f}% - quedan ~{fmt_dur(left)}")
            self.setWindowTitle(f"{pct:.0f}% - {config.APP_NAME}")
        else:
            n = len(self.runner.pending())
            self.l_status.setText(f"{n} en cola" if n else "")
            self.setWindowTitle(config.APP_NAME)
        if self.stack.currentWidget() is self.queue:
            self.queue.refresh()
        if self._dirty or (j and self.stack.currentWidget() is self.studies):
            self._dirty = False
            self.queue.refresh()
            self.studies.refresh()

    def motor_changed(self, m):
        if m["ok"]:
            self.l_motor.setText(f"<span style='color:#4cc38a'>●</span> Motor de IA listo<br>"
                                 f"<span style='font-size:8pt'>{'GPU' if m['cuda'] else 'Procesador'}</span>")
        else:
            self.l_motor.setText("<span style='color:#f2555a'>●</span> Motor de IA no instalado")
            if not getattr(self, "_warned", False):
                self._warned = True
                self.go("equipo")
                QMessageBox.information(self, "Primer uso", "Para procesar estudios hay que instalar el motor de IA "
                                        "(una sola vez). Revisa los requisitos y pulsa 'Instalar motor de IA'.\n\n"
                                        "Los estudios ya procesados se pueden ver en 3D sin el motor.")

    def add_study(self, path):
        if not self.cfg.motor_python():
            QMessageBox.warning(self, "Motor de IA", "Primero instala el motor de IA (pagina 'Equipo y motor').")
            self.go("equipo")
            return
        if AddDialog(self, path).exec():
            self.go("cola")

    def open_viewer(self, sid):
        self.go("visor")
        self.viewer.show_study(sid)

    def job_finished(self, j):
        self.studies.refresh()
        self.queue.refresh()
        if j.estado == "listo":
            self.statusBar().showMessage(f"{j.nombre}: terminado en {fmt_dur(j.fin - j.inicio)}", 15000)
            QApplication_alert(self)
            if not self.runner.pending():
                r = QMessageBox.question(self, "Estudio listo", f"{j.nombre} ya esta reconstruido "
                                         f"({fmt_dur(j.fin - j.inicio)}). ¿Verlo en 3D ahora?")
                if r == QMessageBox.Yes:
                    self.open_viewer(j.sid)
        elif j.estado == "error":
            QApplication_alert(self)
            QMessageBox.critical(self, "Error al procesar", f"{j.nombre}: {j.error}\n\nEl registro completo esta en:\n{j.log_path or config.LOGS}")

    def toggle_full(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.add_study(urls[0])

    def closeEvent(self, e):
        if self.runner.busy() or self.system.installer:
            what = "un estudio en proceso" if self.runner.busy() else "la instalacion del motor en curso"
            if QMessageBox.question(self, "Salir", f"Hay {what}. Si sales se cancelara. ¿Salir igualmente?") != QMessageBox.Yes:
                e.ignore()
                return
            self.runner.cancel()
            if self.system.installer:
                self.system.installer.cancel()
        self.server.stop()
        e.accept()


def QApplication_alert(w):
    from PySide6.QtWidgets import QApplication
    QApplication.alert(w, 0)
