"""The link graph window: chats, projects and personalities as nodes you can drag and link.

Chats are chat bubbles, projects squares and personalities triangles (chat_links.py
has what the links mean, graph_layout.py the physics). The window only draws and
edits ChatMain.links; ChatMain.links_updated saves them and lets the open chat
follow, then links_changed redraws the graph from them.

  Drag a node         moves it and pins it where you drop it
  Click a node        selects it; the panel on the right shows what you can do with it
  Double-click a node starts a link: click its target, or Esc or empty space to cancel
  Hover over a node   shows its direct links and dims the rest
  Right-click         a menu for that node or link
  Scroll              zooms; dragging empty space moves the view
"""

import math

import chat_links as cl
import graph_layout
import paths
import personalities

from PySide6.QtCore import QLineF, QPointF, QRectF, QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QFontMetricsF, QPainter, QPainterPath,
                           QPainterPathStroker, QPen, QPixmap, QPolygonF)
from PySide6.QtWidgets import (QApplication, QCheckBox, QGraphicsItem, QGraphicsLineItem, QGraphicsPathItem,
                               QGraphicsScene, QGraphicsView, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QMainWindow, QMenu, QMessageBox, QPushButton, QVBoxLayout, QWidget)

BACKGROUND = "#232323"
TEXT = "#f0f0f0"
MUTED = "#9a9a9a"
COLORS = {cl.CHAT: "#6aa9ff", cl.PROJECT: "#f2b84b", cl.PERSONALITY: "#e57ab0"}
SIZES = {cl.CHAT: 13.0, cl.PROJECT: 19.0, cl.PERSONALITY: 23.0}
TYPE_NAMES = {cl.CHAT: "Chat", cl.PROJECT: "Project", cl.PERSONALITY: "Personality"}
LINK_LABELS = {cl.MEMBER: "is part of", cl.SPEAKS: "speaks as", cl.READS: "can be read by"}
DIM = 0.16                # how far what you aren't looking at fades
FADE_SPEED = 0.25
LABEL_ZOOM = (0.45, 0.75) # names fade out below this zoom, unless you're pointing at them
ZOOM_RANGE = (0.15, 3.0)
FRAME_MS = 33
STYLE = (f"QWidget{{background:{BACKGROUND};color:{TEXT}}}"
         "QLineEdit,QPushButton,QListWidget{background:rgb(57,57,57);color:white;border:1px solid rgb(90,90,90);padding:3px}"
         "QPushButton:hover{background:rgb(70,70,70)}"
         "QCheckBox::indicator{width:13px;height:13px;border:1px solid rgb(170,170,170);background:rgb(57,57,57)}"
         "QCheckBox::indicator:checked{background:rgb(106,169,255)}"
         "QMenu{background:rgb(45,45,45);color:white}")
MENU_STYLE = "QMenu{background:rgb(45,45,45);color:white}QMenu::item:selected{background:rgb(70,70,70)}"


def node_path(kind: str, r: float) -> QPainterPath:
    """The shape of a node around (0, 0): chat bubble, square or triangle."""
    path = QPainterPath()
    if kind == cl.CHAT:
        path.addEllipse(QPointF(0, -r * 0.08), r, r * 0.88)
        tail = QPainterPath()
        tail.addPolygon(QPolygonF([QPointF(-r * 0.55, r * 0.45), QPointF(-r * 0.7, r * 1.15), QPointF(r * 0.05, r * 0.72)]))
        tail.closeSubpath()
        path = path.united(tail)
    elif kind == cl.PROJECT:
        side = r * 0.9
        path.addRoundedRect(QRectF(-side, -side, side * 2, side * 2), r * 0.16, r * 0.16)
    else:
        path.addPolygon(QPolygonF([QPointF(0, -r * 1.1), QPointF(r, r * 0.68), QPointF(-r, r * 0.68)]))
        path.closeSubpath()
    return path


def type_icon(kind: str, size=18) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.translate(size / 2, size / 2)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(COLORS[kind]))
    painter.drawPath(node_path(kind, size * 0.4))
    painter.end()
    return pixmap


class Fader:
    """Eases an item's opacity towards `target`, a little each frame."""
    target = 1.0

    def fade_step(self) -> bool:
        now = self.opacity()
        if abs(now - self.target) < 0.01:
            if now != self.target:
                self.setOpacity(self.target)
            return False
        self.setOpacity(now + (self.target - now) * FADE_SPEED)
        return True


class NodeItem(Fader, QGraphicsItem):
    def __init__(self, name: str, graph):
        super().__init__()
        self.name = name
        self.kind, self.label = cl.split_node(name)
        self.graph = graph
        self.radius = SIZES[self.kind]
        self.path = node_path(self.kind, self.radius)
        self.color = QColor(COLORS[self.kind])
        self.glow = False          # a valid link target, or what you're pointing at
        self.setZValue(2)

    def set_links(self, count: int):
        """Busier nodes are bigger."""
        radius = SIZES[self.kind] + min(math.sqrt(count) * 2.6, 20.0)
        if radius != self.radius:
            self.prepareGeometryChange()
            self.radius = radius
            self.path = node_path(self.kind, radius)

    def boundingRect(self):
        r = self.radius + 8
        return QRectF(-140, -r - 4, 280, r * 2 + 34)

    def shape(self):
        return self.path

    def paint(self, painter, _option, _widget=None):
        graph = self.graph
        if self.glow:
            painter.setPen(QPen(QColor(255, 255, 255, 90), 7))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(self.path)
        chat = self.label if self.kind == cl.CHAT else cl.lead_of(graph.links, self.label) \
            if self.kind == cl.PROJECT else None
        if self.name == graph.selected:
            pen = QPen(QColor("#ffffff"), 2.6)
        elif chat is not None and chat == graph.chat_main.chat_name():
            pen = QPen(QColor("#ffffff"), 1.6)       # the open chat
        else:
            pen = QPen(self.color.darker(150), 1.2)
        if chat is not None and chat in graph.links["Private"]:
            pen.setStyle(Qt.DashLine)                # private: no personality can read it
            pen.setColor(QColor("#ffffff"))
        painter.setPen(pen)
        painter.setBrush(QBrush(self.color))
        painter.drawPath(self.path)
        if self.name in graph.layout.pinned:         # a small pin mark
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(BACKGROUND))
            painter.drawEllipse(QPointF(0, 0), 2.6, 2.6)

        low, high = LABEL_ZOOM
        scale = painter.worldTransform().m11()
        alpha = 1.0 if self.glow or self.name == graph.selected else min(max((scale - low) / (high - low), 0.0), 1.0)
        if alpha > 0:
            painter.setOpacity(painter.opacity() * alpha)
            painter.setPen(QColor(TEXT))
            painter.setFont(graph.label_font)
            text = graph.label_metrics.elidedText(graph.shown_name(self.name), Qt.ElideRight, 260)
            width = graph.label_metrics.horizontalAdvance(text)
            painter.drawText(QPointF(-width / 2, self.radius + 19), text)


class EdgeItem(Fader, QGraphicsPathItem):
    ARROW = 9.0

    def __init__(self, link: dict, start: NodeItem, end: NodeItem, offset: float):
        super().__init__()
        self.link = link
        self.start, self.end = start, end
        self.offset = offset        # sideways, when a chat has two links to one personality
        self.selected = False
        self.arrow = None
        self.setZValue(1)
        self.set_style()
        self.setToolTip(f"{start.label} {LINK_LABELS[link['Kind']]} {end.label}")

    def set_style(self):
        kind = self.link["Kind"]
        width = {cl.MEMBER: 1.7, cl.SPEAKS: 3.0, cl.READS: 1.7}[kind] + (1.4 if self.selected else 0)
        color = QColor("#ffffff") if self.selected else QColor(
            {cl.MEMBER: "#a7a7a7", cl.SPEAKS: COLORS[cl.PERSONALITY], cl.READS: COLORS[cl.PERSONALITY]}[kind])
        pen = QPen(color, width)
        if kind == cl.READS:
            pen.setStyle(Qt.DashLine)
        pen.setCapStyle(Qt.RoundCap)
        self.setPen(pen)
        self.update()

    def refresh(self):
        a, b = self.start.pos(), self.end.pos()
        line = QLineF(a, b)
        length = line.length()
        path = QPainterPath()
        self.arrow = None
        if length < 1:
            self.setPath(path)
            return
        ux, uy = (b.x() - a.x()) / length, (b.y() - a.y()) / length
        side = QPointF(-uy * self.offset, ux * self.offset)
        tip = a + QPointF(ux, uy) * (self.start.radius + 4) + side
        tail = b - QPointF(ux, uy) * (self.end.radius + 4) + side
        path.moveTo(tip)
        path.lineTo(tail)
        self.setPath(path)
        if self.link["Kind"] == cl.READS:      # it points from the reader at what it reads
            back = QPointF(ux, uy) * self.ARROW
            wing = QPointF(-uy, ux) * (self.ARROW * 0.5)
            self.arrow = QPolygonF([tip, tip + back + wing, tip + back - wing])

    def boundingRect(self):
        return super().boundingRect().adjusted(-12, -12, 12, 12)

    def shape(self):
        stroker = QPainterPathStroker()
        stroker.setWidth(11)
        return stroker.createStroke(self.path())

    def paint(self, painter, option, widget=None):
        painter.setPen(self.pen())
        painter.drawPath(self.path())
        if self.arrow is not None:
            painter.setPen(Qt.NoPen)
            painter.setBrush(self.pen().color())
            painter.drawPolygon(self.arrow)


class GraphView(QGraphicsView):
    """Turns the mouse into what the window should do."""

    def __init__(self, graph):
        super().__init__(graph.scene)
        self.graph = graph
        self.setRenderHint(QPainter.Antialiasing)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setBackgroundBrush(QColor(BACKGROUND))
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._pressed = None         # (where, the item under it, or None for empty space)
        self._moved = False
        self._last = None
        self._link_ended = False     # that press ended a link, so its double-click starts nothing

    def item_at(self, pos):
        for item in self.items(pos):
            if isinstance(item, NodeItem):
                return item
        for item in self.items(pos):
            if isinstance(item, EdgeItem):
                return item
        return None

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self.setFocus()
        pos = event.position().toPoint()
        item = self.item_at(pos)
        self._link_ended = False
        if self.graph.link_from:
            self._link_ended = True
            self.graph.finish_link(item if isinstance(item, NodeItem) else None)
            return
        self._pressed = (pos, item)
        self._moved = False
        self._last = pos

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        graph = self.graph
        if graph.link_from:
            graph.move_link_line(self.mapToScene(pos))
            item = self.item_at(pos)
            graph.set_hover(item.name if isinstance(item, NodeItem) else None)
            return
        if self._pressed is None:
            item = self.item_at(pos)
            graph.set_hover(item.name if isinstance(item, NodeItem) else None)
            return
        start, item = self._pressed
        if not self._moved and (pos - start).manhattanLength() < QApplication.startDragDistance():
            return
        self._moved = True
        if isinstance(item, NodeItem):
            graph.drag_node(item, self.mapToScene(pos))
        elif item is None:
            delta = pos - self._last
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
        self._last = pos

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self._pressed is None:
            return
        _, item = self._pressed
        self._pressed = None
        if self._moved and isinstance(item, NodeItem):
            self.graph.drop_node(item)
        elif not self._moved:
            self.graph.select(item)

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.LeftButton or self._link_ended:
            return
        item = self.item_at(event.position().toPoint())
        if isinstance(item, NodeItem):
            self._pressed = None
            self.graph.start_link(item)

    def wheelEvent(self, event):
        factor = 1.0015 ** event.angleDelta().y()
        scale = self.transform().m11() * factor
        self.graph._fit_when_settled = False
        if ZOOM_RANGE[0] <= scale <= ZOOM_RANGE[1]:
            self.scale(factor, factor)

    def contextMenuEvent(self, event):
        if self.graph.link_from:
            self.graph.cancel_link()
            return
        self.graph.show_menu(self.item_at(event.pos()), event.globalPos())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.graph.cancel_link()
        elif event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.graph.remove_selected_link()
        else:
            super().keyPressEvent(event)

    def leaveEvent(self, event):
        self.graph.set_hover(None)
        super().leaveEvent(event)


class LinkGraph(QMainWindow):
    edit_personality = Signal(str)      # main.py opens the settings window on it

    def __init__(self, chat_main):
        super().__init__()
        self.chat_main = chat_main
        self.setWindowTitle("Link Graph")
        self.setStyleSheet(STYLE)
        self.resize(1100, 720)
        self.nodes = {}              # node name -> NodeItem
        self.edges = []
        self.layout = graph_layout.Layout()
        self.hover = None
        self.selected = None         # a node's name
        self.selected_edge = None
        self.held = None             # the node being dragged
        self.link_from = None        # the node a new link starts from
        self.link_line = None
        self._fit_when_settled = False   # just opened: frame the graph once it has spread out
        self.label_font = QFont()
        self.label_font.setPointSizeF(9.0)
        self.label_metrics = QFontMetricsF(self.label_font)
        self.scene = QGraphicsScene(self)
        self.scene.setSceneRect(-20000, -20000, 40000, 40000)
        self.view = GraphView(self)
        self.timer = QTimer(self)
        self.timer.setInterval(FRAME_MS)
        self.timer.timeout.connect(self.tick)
        self._build_window()
        chat_main.links_changed.connect(self.on_links_changed)
        chat_main.chat_list.sections_changed.connect(self.on_sections_changed)

    @property
    def links(self):
        return self.chat_main.links

    def shown_name(self, name) -> str:
        """A node's label: a chat you sorted into a section of the chat list reads Section/Chat."""
        kind, label = cl.split_node(name)
        if kind == cl.CHAT:
            section = next((s["Name"] for s in self.chat_main.chat_list.sections if label in s["Chats"]), None)
            if section:
                return f"{section}/{label}"
        return label

    # ------------------------------------------------------------------
    # The window around the graph
    # ------------------------------------------------------------------

    def _build_window(self):
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a node by name…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(220)
        self.search.textChanged.connect(lambda _: self.refresh())
        bar.addWidget(self.search)
        self.unlinked_check = QCheckBox("Show unlinked chats")
        self.unlinked_check.setChecked(QSettings().value("graph/show_unlinked", False, type=bool))
        self.unlinked_check.toggled.connect(self.on_unlinked_toggled)
        bar.addWidget(self.unlinked_check)
        self.hint = QLabel("")
        self.hint.setStyleSheet(f"color:{MUTED}")
        bar.addWidget(self.hint, 1)
        for label, slot in (("New project…", self.new_project), ("New personality…", self.new_personality),
                            ("Fit to window", self.fit_view)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            bar.addWidget(button)

        self.panel = QWidget()
        self.panel.setFixedWidth(270)
        self.panel_layout = QVBoxLayout(self.panel)
        self.panel_layout.setContentsMargins(10, 0, 0, 0)

        body = QHBoxLayout()
        body.addWidget(self.view, 1)
        body.addWidget(self.panel)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addLayout(bar)
        layout.addLayout(body, 1)
        self.setCentralWidget(root)
        self.show_panel()

    def open(self):
        first = not self.isVisible()
        self.refresh()
        self.show()
        self.raise_()
        self.activateWindow()
        self._fit_when_settled = first
        if first:
            QTimer.singleShot(0, self.fit_view)
        self.wake()

    def closeEvent(self, event):
        self.save_layout()
        self.timer.stop()
        self.cancel_link()
        super().closeEvent(event)

    def on_links_changed(self):
        if self.isVisible():
            self.refresh()

    def on_sections_changed(self, _sections):
        if self.isVisible():
            self.update_states()
            self.show_panel()

    def on_unlinked_toggled(self, on):
        QSettings().setValue("graph/show_unlinked", on)
        self.refresh()
        QTimer.singleShot(0, self.fit_view)

    def fit_view(self):
        if not self.nodes:
            return
        rect = QRectF()
        for item in self.nodes.values():
            rect = rect.united(QRectF(item.pos() - QPointF(70, 50), item.pos() + QPointF(70, 60)))
        self.view.fitInView(rect, Qt.KeepAspectRatio)
        if self.view.transform().m11() > 1.2:
            self.view.resetTransform()
            self.view.scale(1.2, 1.2)
            self.view.centerOn(rect.center())

    # ------------------------------------------------------------------
    # Drawing the graph from the links
    # ------------------------------------------------------------------

    def _shown_nodes(self) -> list:
        """Hubs and linked chats always; the open chat so it can be linked; unlinked ones if asked or searched for."""
        links = self.links
        query = self.search.text().strip().lower()
        shown = [cl.node(cl.PROJECT, p) for p in links["Projects"]]
        shown += [cl.node(cl.PERSONALITY, p) for p in links["Personalities"]]
        leads = set(links["Leads"].values())      # drawn as their projects
        for chat in self.chat_main.all_chats():
            if chat in leads:
                continue
            if (self.unlinked_check.isChecked() or cl.is_linked(links, chat) or chat == self.chat_main.chat_name()
                    or (query and query in self.shown_name(cl.node(cl.CHAT, chat)).lower())):
                shown.append(cl.node(cl.CHAT, chat))
        return shown

    def refresh(self):
        """Match the scene to the links, keeping every node that stays where it is."""
        links = self.links
        shown = self._shown_nodes()
        keep = set(shown)
        for name in [n for n in self.nodes if n not in keep]:
            self.scene.removeItem(self.nodes.pop(name))
            self.layout.remove(name)
        if self.selected not in keep:
            self.selected = None
        new = [n for n in shown if n not in self.nodes]
        pairs = {}
        for link in links["Links"]:
            ends = (cl.shown_node(links, link["From"]), cl.shown_node(links, link["To"]))
            pairs.setdefault(ends, []).append(link)
        neighbors = {}
        for start, end in pairs:
            neighbors.setdefault(start, []).append(end)
            neighbors.setdefault(end, []).append(start)
        moved = False
        for i, name in enumerate(new):
            item = NodeItem(name, self)
            self.nodes[name] = item
            self.scene.addItem(item)
            saved = links["Layout"].get(name)
            if saved:
                self.layout.place(name, saved["x"], saved["y"], saved["Pinned"])
            else:
                moved = True
                x, y = self._start_place(name, i, neighbors)
                self.layout.place(name, x, y)
            item.setPos(self.layout.x[name], self.layout.y[name])

        old_edges = {(e.start.name, e.end.name) for e in self.edges}
        for edge in self.edges:
            self.scene.removeItem(edge)
        self.edges = []
        keep_edge = None
        for (start, end), group in pairs.items():
            if start in self.nodes and end in self.nodes:
                moved = moved or (start, end) not in old_edges
                for i, link in enumerate(group):
                    offset = (i - (len(group) - 1) / 2) * 7
                    edge = EdgeItem(link, self.nodes[start], self.nodes[end], offset)
                    self.scene.addItem(edge)
                    self.edges.append(edge)
                    edge.refresh()
                    if self.selected_edge == (link["From"], link["To"], link["Kind"]):
                        edge.selected = True
                        edge.set_style()
                        keep_edge = self.selected_edge
        self.selected_edge = keep_edge
        self.layout.edges = [(e.start.name, e.end.name) for e in self.edges]
        for name, item in self.nodes.items():
            item.set_links(sum(1 for e in self.edges if name in (e.start.name, e.end.name)))
        if moved:
            self.layout.warm(1.0 if new else 0.5)
        elif not self.timer.isActive():
            self.layout.heat = 0.0       # nothing new, so what's saved stays exactly where it was
        self.update_states()
        self.show_panel()
        self.wake()

    def _start_place(self, name, index, neighbors):
        """Next to something it links to if that's placed; else around the middle, on a spiral."""
        angle = index * 2.399963
        near = next((n for n in neighbors.get(name, []) if n in self.layout.x), None)
        if near:
            return (self.layout.x[near] + math.cos(angle) * 90, self.layout.y[near] + math.sin(angle) * 90)
        radius = 60 + 30 * math.sqrt(len(self.layout.x) + index)
        return math.cos(angle) * radius, math.sin(angle) * radius

    def update_states(self):
        """Who's bright and who's dim, from what you're pointing at, the link being made and the search."""
        query = self.search.text().strip().lower()
        focus = self.hover or self.selected
        if self.link_from:
            bright = {n for n in self.nodes if n == self.link_from or self._link_choices(self.link_from, n)}
            bright_edges = set()
        elif focus:
            bright = {focus}
            bright_edges = set()
            for edge in self.edges:
                if focus in (edge.start.name, edge.end.name):
                    bright.update((edge.start.name, edge.end.name))
                    bright_edges.add(id(edge))
        elif query:
            bright = {n for n in self.nodes if query in self.shown_name(n).lower()}
            bright_edges = set()
        else:
            bright = None
            bright_edges = None
        for name, item in self.nodes.items():
            item.target = 1.0 if bright is None or name in bright else DIM
            glow = name == self.hover or (self.link_from is not None and name in bright and name != self.link_from)
            if glow != item.glow:
                item.glow = glow
            item.update()
        for edge in self.edges:
            on = bright_edges is None or id(edge) in bright_edges or edge.selected
            edge.target = 1.0 if on else DIM * 0.6
        self.wake()

    # ------------------------------------------------------------------
    # Animation
    # ------------------------------------------------------------------

    def wake(self):
        if not self.timer.isActive() and self.isVisible():
            self.timer.start()

    def tick(self):
        busy = False
        if not self.layout.settled or self.held:
            self.layout.step(self.held)
            for name, item in self.nodes.items():
                item.setPos(self.layout.x[name], self.layout.y[name])
            for edge in self.edges:
                edge.refresh()
            if self.layout.settled and not self.held:
                self.save_layout()
                if self._fit_when_settled:
                    self._fit_when_settled = False
                    self.fit_view()
            busy = True
        for item in (*self.nodes.values(), *self.edges):
            if item.fade_step():
                busy = True
        if not busy:
            self.timer.stop()

    def save_layout(self):
        changed = False
        for name in self.layout.x:
            place = {"x": round(self.layout.x[name], 1), "y": round(self.layout.y[name], 1),
                     "Pinned": name in self.layout.pinned}
            if self.links["Layout"].get(name) != place:
                self.links["Layout"][name] = place
                changed = True
        if changed:
            self.chat_main.save_links()

    # ------------------------------------------------------------------
    # What the mouse does
    # ------------------------------------------------------------------

    def set_hover(self, name):
        if name != self.hover:
            self.hover = name
            self.update_states()

    def drag_node(self, item, where):
        self._fit_when_settled = False
        self.held = item.name
        self.layout.place(item.name, where.x(), where.y(), item.name in self.layout.pinned)
        item.setPos(where)
        self.layout.warm(0.25)     # the others make room, gently
        self.wake()

    def drop_node(self, item):
        self.held = None
        self.layout.place(item.name, item.x(), item.y(), pinned=True)
        item.update()
        self.layout.warm(0.15)
        self.save_layout()
        self.wake()

    def select(self, item):
        self.selected = item.name if isinstance(item, NodeItem) else None
        for edge in self.edges:
            on = edge is item
            if on != edge.selected:
                edge.selected = on
                edge.set_style()
        self.selected_edge = ((item.link["From"], item.link["To"], item.link["Kind"])
                              if isinstance(item, EdgeItem) else None)
        self.update_states()
        self.show_panel()

    # ------------------------------------------------------------------
    # Making links
    # ------------------------------------------------------------------

    def _link_choices(self, a, b) -> list:
        """What linking these could still mean: their kinds, without the links they already have."""
        return [k for k in cl.link_kinds(a, b) if not cl.has_link(self.links, a, b, k)]

    def start_link(self, item):
        self.cancel_link()
        self.link_from = item.name
        self.link_line = QGraphicsLineItem()
        pen = QPen(QColor("#ffffff"), 1.6)
        pen.setStyle(Qt.DotLine)
        self.link_line.setPen(pen)
        self.link_line.setZValue(3)
        self.scene.addItem(self.link_line)
        self.move_link_line(item.pos())
        self.hint.setText(f"Linking {item.label}: click a lit node. Esc cancels.")
        self.update_states()

    def move_link_line(self, where):
        if self.link_from in self.nodes:
            self.link_line.setLine(QLineF(self.nodes[self.link_from].pos(), where))

    def cancel_link(self):
        if self.link_line is not None:
            self.scene.removeItem(self.link_line)
        self.link_line = None
        self.link_from = None
        self.hint.setText("")
        self.update_states()

    def finish_link(self, target):
        """A click while linking: on a lit node it links, on empty space it cancels, on a dim node it waits."""
        if target is None or target.name == self.link_from:
            self.cancel_link()
            return
        start = self.link_from
        choices = self._link_choices(start, target.name)
        if not choices:
            self.hint.setText(f"{cl.split_node(start)[1]} can't link to {target.label}. "
                              "Pick a lit node, or Esc to cancel.")
            return
        self.cancel_link()
        kind = choices[0] if len(choices) == 1 else self._choose_kind(start, target.name, choices)
        if kind == cl.MAKE_PROJECT:
            self._make_project(start, target.name)
        elif kind:
            if kind == cl.SPEAKS:
                project = next((cl.split_node(n)[1] for n in (start, target.name)
                                if cl.split_node(n)[0] == cl.PROJECT), None)
                if project:
                    self.chat_main.project_chat(project)      # a project speaks through its own chat
            cl.add_link(self.links, start, target.name, kind)
            self.chat_main.links_updated()

    def _choose_kind(self, a, b, choices):
        chat = cl.split_node(b if cl.split_node(a)[0] == cl.PERSONALITY else a)[1]
        personality = cl.split_node(a if cl.split_node(a)[0] == cl.PERSONALITY else b)[1]
        speaker = f"{chat}'s own chat" if cl.PROJECT in (cl.split_node(a)[0], cl.split_node(b)[0]) else chat
        labels = {cl.SPEAKS: f"{speaker} speaks as {personality}", cl.READS: f"{personality} can read {chat}"}
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLE)
        actions = {menu.addAction(labels[k]): k for k in choices}
        chosen = menu.exec(self.cursor().pos())
        return actions.get(chosen)

    def _make_project(self, a, b):
        names = " and ".join(cl.split_node(n)[1] for n in (a, b))
        name = self._ask_name("New project", f"Two chats link through a project. Name one for {names}:")
        if name:
            cl.add_hub(self.links, cl.PROJECT, name)
            target = cl.node(cl.PROJECT, name)
            cl.add_link(self.links, a, target, cl.MEMBER)
            cl.add_link(self.links, b, target, cl.MEMBER)
            self.chat_main.links_updated()

    # ------------------------------------------------------------------
    # Node and link actions (the right-click menu and the panel share these)
    # ------------------------------------------------------------------

    def _actions(self, name) -> list:
        """(label, what it does) for a node."""
        kind, label = cl.split_node(name)
        actions = [("Start a link", lambda: self.start_link(self.nodes[name]))]
        if kind == cl.CHAT:
            private = label in self.links["Private"]
            actions.insert(0, ("Open chat", lambda: self.open_chat(label)))
            actions.append(("Stop keeping private" if private else "Keep private from personalities",
                            lambda: self.set_private(label, not private)))
        else:
            if kind == cl.PROJECT:
                actions.insert(0, ("Open project chat", lambda: self.open_project_chat(label)))
                if cl.lead_of(self.links, label):
                    actions.insert(1, ("Start project chat fresh…", lambda: self.chat_main.ask_start_fresh(label)))
            if kind == cl.PERSONALITY:
                actions.append(("Edit settings…", lambda: self.edit_personality.emit(label)))
            actions += [("Open memory folder", lambda: self.open_memory(kind, label)),
                        ("Rename…", lambda: self.rename_hub(kind, label)),
                        ("Delete…", lambda: self.delete_hub(kind, label))]
        if name in self.layout.pinned:
            actions.append(("Unpin", lambda: self.unpin(name)))
        return actions

    def show_menu(self, item, where):
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLE)
        if isinstance(item, NodeItem):
            self.select(item)
            for label, action in self._actions(item.name):
                menu.addAction(label, action)
        elif isinstance(item, EdgeItem):
            self.select(item)
            menu.addAction("Remove link", self.remove_selected_link)
        else:
            menu.addAction("New project…", self.new_project)
            menu.addAction("New personality…", self.new_personality)
            menu.addAction("Fit to window", self.fit_view)
        menu.exec(where)

    def open_chat(self, chat):
        main = self.chat_main
        main.qs_chat(chat)
        main.show()
        main.raise_()
        main.activateWindow()

    def open_project_chat(self, project):
        main = self.chat_main
        main.open_project_chat(project)
        main.show()
        main.raise_()
        main.activateWindow()

    def set_private(self, chat, private):
        cl.set_private(self.links, chat, private)
        self.chat_main.links_updated()

    def set_followed(self, chat, group, on):
        skipped = [g for g in cl.skipped_of(self.links, chat) if g != group] + ([] if on else [group])
        cl.set_skipped(self.links, chat, skipped)
        self.chat_main.links_updated()

    def unpin(self, name):
        self.layout.pinned.discard(name)
        self.layout.warm(0.4)
        self.save_layout()
        self.nodes[name].update()
        self.show_panel()
        self.wake()

    def remove_selected_link(self):
        if self.selected_edge:
            start, end, kind = self.selected_edge
            self.selected_edge = None
            cl.remove_link(self.links, start, end, kind)
            self.chat_main.links_updated()

    def _ask_name(self, title, label, text=""):
        name, ok = QInputDialog.getText(self, title, label, text=text)
        return cl.clean_name(name) if ok else ""

    def _confirm(self, title, text) -> bool:
        return QMessageBox.question(self, title, text) == QMessageBox.Yes

    def new_project(self):
        name = self._ask_name("New project", "Project name")
        if name and cl.add_hub(self.links, cl.PROJECT, name):
            self.chat_main.links_updated()

    def new_personality(self):
        name = self._ask_name("New personality", "Personality name")
        if name and cl.add_hub(self.links, cl.PERSONALITY, name):
            personalities.save(name, {**self.chat_main.settings, "Character": name, "Pretext": "", "Model": "",
                                      "TTS": False, "Avatar": False})
            self.chat_main.links_updated()
            self.edit_personality.emit(name)

    @staticmethod
    def _folder(kind, name):
        return (paths.PROJECT_DIR if kind == cl.PROJECT else paths.PERSONALITY_DIR) / name

    def open_memory(self, kind, name):
        folder = self._folder(kind, name) / "memory"
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def rename_hub(self, kind, old):
        new = self._ask_name(f"Rename {TYPE_NAMES[kind].lower()}", "New name", old)
        if not new or new == old:
            return
        taken = new in self.links["Projects" if kind == cl.PROJECT else "Personalities"]
        if taken or not personalities.rename_folder(self._folder(kind, old), self._folder(kind, new)):
            QMessageBox.warning(self, "Rename", f"There's already one called {new}.")
            return
        was = cl.node(kind, old)
        cl.rename_node(self.links, kind, old, new)
        if self.selected == was:
            self.selected = cl.node(kind, new)
        self.chat_main.links_updated()

    def delete_hub(self, kind, name):
        if not self._confirm(f"Delete {TYPE_NAMES[kind].lower()}",
                             f"Delete {name}? Its chats stay, and chats speaking as it go back to their own "
                             "settings. Its folder and memory notes go to the Recycle Bin."):
            return
        cl.remove_hub(self.links, kind, name)
        try:
            personalities.remove_folder(self._folder(kind, name))
        except OSError as e:
            print(f"Could not move the folder of {name}: {e}")
        self.chat_main.links_updated()

    # ------------------------------------------------------------------
    # The panel on the right
    # ------------------------------------------------------------------

    def _clear_panel(self, layout=None):
        layout = layout or self.panel_layout
        while layout.count():
            child = layout.takeAt(0)
            if child.layout():
                self._clear_panel(child.layout())
                child.layout().deleteLater()
            elif child.widget():
                child.widget().hide()         # deleteLater alone leaves it on show until the next event loop
                child.widget().deleteLater()

    def _panel_label(self, text, muted=False, bold=False):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextFormat(Qt.PlainText)
        label.setStyleSheet(("color:" + (MUTED if muted else TEXT)) + (";font-weight:700;font-size:15px" if bold else ""))
        self.panel_layout.addWidget(label)
        return label

    def _legend_row(self, kind, text):
        row = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(type_icon(kind))
        icon.setFixedWidth(22)
        row.addWidget(icon)
        row.addWidget(QLabel(text), 1)
        self.panel_layout.addLayout(row)

    def show_panel(self):
        self._clear_panel()
        if self.selected_edge:
            self._show_edge_panel()
        elif self.selected in self.nodes:
            self._show_node_panel(self.selected)
        else:
            self._show_help_panel()
        self.panel_layout.addStretch(1)

    def _show_help_panel(self):
        self._panel_label("Link Graph", bold=True)
        self._legend_row(cl.CHAT, "Chat")
        self._legend_row(cl.PROJECT, "Project: chats that share context")
        self._legend_row(cl.PERSONALITY, "Personality: who a chat talks as")
        self._panel_label("Solid line: part of a project\nThick line: speaks as\nDashed arrow: can read", muted=True)
        self._panel_label("Drag a node to move and pin it. Double-click a node, then click another, to link them. "
                          "Click a node for its details and right-click for its menu. Scroll to zoom; drag empty "
                          "space to move around.", muted=True)
        if not self.links["Links"]:
            self._panel_label("Nothing is linked yet. Make a project or personality with the buttons above, and "
                              "turn on Show unlinked chats to see your chats.")

    def _show_edge_panel(self):
        start, end, kind = self.selected_edge
        self._panel_label("Link", bold=True)
        self._panel_label(f"{cl.split_node(start)[1]} {LINK_LABELS[kind]} {cl.split_node(end)[1]}")
        button = QPushButton("Remove link")
        button.clicked.connect(self.remove_selected_link)
        self.panel_layout.addWidget(button)

    @staticmethod
    def _link_row(name, link) -> str:
        """A link as the selected node sees it."""
        lower = link["From"] == name
        other = cl.split_node(link["To"] if lower else link["From"])[1]
        return {(cl.MEMBER, True): f"Part of {other}", (cl.MEMBER, False): f"Session: {other}",
                (cl.SPEAKS, True): f"Speaks as {other}", (cl.SPEAKS, False): f"{other} speaks as this",
                (cl.READS, True): f"{other} can read this", (cl.READS, False): f"Can read {other}"}[link["Kind"], lower]

    def _show_node_panel(self, name):
        kind, label = cl.split_node(name)
        self._panel_label(self.shown_name(name), bold=True)
        self._panel_label(TYPE_NAMES[kind], muted=True)
        rows = [self._link_row(name, link) for link in cl.links_of(self.links, name)]
        lead = cl.lead_of(self.links, label) if kind == cl.PROJECT else None
        if lead:
            rows += [self._link_row(cl.node(cl.CHAT, lead), link)
                     for link in cl.links_of(self.links, cl.node(cl.CHAT, lead))]
        if rows:
            listing = QListWidget()
            listing.addItems(rows)
            listing.setMaximumHeight(170)
            self.panel_layout.addWidget(listing)
        speaker = label if kind == cl.CHAT else lead
        personality = cl.speaks_as(self.links, speaker) if speaker else None
        if kind == cl.PROJECT:
            count = len(cl.sessions_of(self.links, label))
            sessions = f"{count} session{'' if count == 1 else 's'}"
            self._panel_label(f"Its own chat hands out work to its {sessions}: open it and say what you need."
                              if lead else f"{sessions}. Open the project chat to hand out work to them.", muted=True)
        if personality:
            self._panel_label(f"Name, description and memory notes come from {personality}. Also take:", muted=True)
            skipped = cl.skipped_of(self.links, label)
            for group in personalities.OPTIONAL:
                if kind == cl.PROJECT and group == "LLM model":
                    continue                 # a project's chat keeps a model that thinks
                check = QCheckBox(group)
                check.setChecked(group not in skipped)
                check.toggled.connect(lambda on, c=speaker, g=group: self.set_followed(c, g, on))
                self.panel_layout.addWidget(check)
        if kind == cl.CHAT:
            reach = cl.readable_chats(self.links, label)
            self._panel_label(f"Can read {len(reach)} other chat{'' if len(reach) == 1 else 's'}."
                              + (" Private: no personality can read it." if label in self.links["Private"] else ""),
                              muted=True)
        for text, action in self._actions(name):
            button = QPushButton(text)
            button.clicked.connect(action)
            self.panel_layout.addWidget(button)
