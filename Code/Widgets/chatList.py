"""The chat list on the left: sections you make and drag chats into (chat_sections.py),
and a group for each project holding its chats.

Dragging only says what moves where. The list is always redrawn from the
sections and the projects' links, so chats keep their most-recently-used order
inside each group. A project's chats can't be dragged out of it, but the project
can be dragged into a section. Clicking a project opens its own chat, where its
work is handed out; its arrow opens and closes the group. The row under the
cursor shows an x on its right that deletes it, once ChatMain has asked. A session
that's working, queued or waiting for your OK says so on its right, and so does its
project, so you see it with the group closed.
"""

import chat_sections

from PySide6.QtWidgets import QInputDialog, QMenu, QStyledItemDelegate, QStyleOptionViewItem, QTreeWidget, QTreeWidgetItem
from PySide6.QtCore import QEvent, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor

NEW_CHAT_LABEL = "Create New Chat [+]"
NEW_PROJECT_LABEL = "Start New Project [+]"
NEW_SECTION_LABEL = "Create New Section [+]"
ROW = Qt.UserRole                       # each row's (kind, name); kind is "new chat", "new project", "new section",
                                        # "section", "project" or "chat"
DELETABLE = ("section", "project", "chat")
X_WIDTH = 22                            # the delete x's box at the right of the hovered row
STATES = {"working": ("⏳ working", QColor(120, 200, 140)), "waiting": ("✋ needs OK", QColor(235, 170, 80)),
          "queued": ("🕓 queued", QColor(150, 150, 150))}


class _RowDelegate(QStyledItemDelegate):
    """Draws the hovered row with an x on its right, its text kept clear of it."""

    def __init__(self, tree):
        super().__init__(tree)
        self.tree = tree

    def paint(self, painter, option, index):
        item = self.tree.itemFromIndex(index)
        hovered = item is not None and item is self.tree.hovered and item.data(0, ROW)[0] in DELETABLE
        state = self.tree.state_of_row(item) if item is not None else None
        if not hovered and not state:
            super().paint(painter, option, index)
            return
        right = self.tree.x_rect(option.rect).left() - 2 if hovered else self.tree.viewport().width() - 4
        state_rect = None
        if state:
            text, color = STATES[state]
            width = painter.fontMetrics().horizontalAdvance(text) + 6
            state_rect = QRect(right - width, option.rect.top(), width, option.rect.height())
            right = state_rect.left() - 2
        opt = QStyleOptionViewItem(option)
        opt.rect.setRight(min(opt.rect.right(), right))
        super().paint(painter, opt, index)
        painter.save()
        if state_rect is not None:
            painter.setPen(color)
            painter.drawText(state_rect, Qt.AlignVCenter | Qt.AlignRight, text)
        if hovered:
            painter.setPen(QColor(235, 110, 110) if self.tree.on_x else QColor(170, 170, 170))
            painter.drawText(self.tree.x_rect(option.rect), Qt.AlignCenter, "✕")
        painter.restore()


class ChatList(QTreeWidget):
    chat_clicked = Signal(str)
    new_chat_clicked = Signal()
    project_clicked = Signal(str)       # open the project's own chat
    new_project_requested = Signal(str) # ask for a new project's name; it goes in this section ("" = none)
    sections_changed = Signal(list)     # the sections, after you changed them
    fresh_requested = Signal(str)       # a project whose own chat should start fresh
    delete_requested = Signal(str, str) # (kind, name) of a section, project or chat to delete, once you say yes

    def __init__(self, parent=None):
        super().__init__(parent)
        self.chats = []                 # chat names, oldest use first (.temp.json's ChatList)
        self.sections = []
        # [(project, its own chat or None, its other chats)], in the links' order (ChatMain sets it)
        self.project_groups = lambda: []
        self.chat_state = lambda chat: "idle"   # a session's "working", "waiting", "queued" or "idle" (ChatMain sets it)
        self._closed_projects = set()
        self._press_x = None            # where the last click went down, to tell a project's arrow from its name
        self.hovered = None             # the row under the cursor, which shows the delete x
        self.on_x = False               # the cursor is on that x
        self.setMouseTracking(True)
        self.setItemDelegate(_RowDelegate(self))
        self.setHeaderHidden(True)
        self.setIndentation(14)
        self.setExpandsOnDoubleClick(False)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QTreeWidget.InternalMove)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_menu)
        self.itemClicked.connect(self.on_click)
        self.itemExpanded.connect(lambda item: self._remember_open(item, True))
        self.itemCollapsed.connect(lambda item: self._remember_open(item, False))
        self.fill([], [])

    # ------------------------------------------------------------------

    def fill(self, chats, sections):
        self.chats = list(chats)
        self.sections = chat_sections.normalize(sections, self.chats)
        self._redraw()

    def _redraw(self):
        pos = self.verticalScrollBar().value()
        self.blockSignals(True)
        self.clear()
        for kind, label in (("new chat", NEW_CHAT_LABEL), ("new project", NEW_PROJECT_LABEL),
                            ("new section", NEW_SECTION_LABEL)):
            self._row(self, kind, "", label, Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        chat_flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled
        groups = {}
        in_project = {}                 # chat -> the project it's listed under (its first one)
        for project, lead, chats in self.project_groups():
            groups[project] = (lead, [c for c in chats if c != lead and c not in in_project])
            for chat in [lead, *chats]:
                if chat:
                    in_project.setdefault(chat, project)
        placed = set()
        for s in self.sections:
            projects = [p for p in s.get("Projects", []) if p in groups]
            placed.update(projects)
            members = [c for c in reversed(self.chats) if c in s["Chats"] and c not in in_project]
            item = self._row(self, "section", s["Name"], f"{s['Name']} ({len(projects) + len(members)})", chat_flags)
            self._bold(item)
            for project in projects:
                self._project_row(item, project, *groups[project])
            for chat in members:
                self._chat_row(item, chat, chat_flags)
            item.setExpanded(s["Open"])
        for project in groups:
            if project not in placed:
                self._project_row(self, project, *groups[project])
        for chat in reversed(self.chats):
            if chat not in in_project and not chat_sections.section_of(self.sections, chat):
                self._chat_row(self, chat, chat_flags)
        self.blockSignals(False)
        self.verticalScrollBar().setValue(pos)

    @staticmethod
    def _bold(item):
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)

    def _project_row(self, parent, project, lead, chats):
        """The project's group: clicking it opens its own chat; its chats can't be dragged out."""
        order = {c: i for i, c in enumerate(reversed(self.chats))}
        chats = sorted(chats, key=lambda c: order.get(c, len(order)))
        item = self._row(parent, "project", project, f"📂 {project} ({len(chats)})",
                         Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
        self._bold(item)
        item.setToolTip(0, f"Project {project}: click to open its own chat, where its work is handed out")
        for chat in chats:
            self._row(item, "chat", chat, chat, Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        item.setExpanded(project not in self._closed_projects)
        return item

    def _chat_row(self, parent, chat, flags):
        return self._row(parent, "chat", chat, chat, flags)

    def redraw(self):
        self._redraw()

    def clear(self):
        self.hovered = None             # its rows are about to go
        super().clear()

    @staticmethod
    def _row(parent, kind, name, text, flags):
        item = QTreeWidgetItem(parent, [text])
        item.setData(0, ROW, (kind, name))
        item.setFlags(flags)
        item.setToolTip(0, text)
        return item

    def _changed(self):
        self._redraw()
        self.sections_changed.emit(self.sections)

    def _remember_open(self, item, is_open):
        kind, name = item.data(0, ROW)
        if kind == "project":
            (self._closed_projects.discard if is_open else self._closed_projects.add)(name)
        section = chat_sections.find(self.sections, name) if kind == "section" else None
        if section:
            section["Open"] = is_open
            self.sections_changed.emit(self.sections)

    # ------------------------------------------------------------------
    # The delete x on the hovered row
    # ------------------------------------------------------------------

    def state_of_row(self, item):
        """What a row shows on its right: its session's state, or for a project the busiest of its chats'. None if idle."""
        kind, name = item.data(0, ROW)
        if kind == "chat":
            states = [self.chat_state(name)]
        elif kind == "project":
            states = [self.chat_state(item.child(k).data(0, ROW)[1]) for k in range(item.childCount())]
        else:
            return None
        return next((s for s in STATES if s in states), None)

    def x_rect(self, row_rect):
        """Where the x goes on a row: the right of the list, wherever the row's own text ends."""
        return QRect(self.viewport().width() - X_WIDTH - 4, row_rect.top(), X_WIDTH, row_rect.height())

    def _x_hit(self, pos):
        """The deletable row whose x is at `pos`, or None."""
        item = self.itemAt(pos)
        if item is None or item.data(0, ROW)[0] not in DELETABLE:
            return None
        return item if self.x_rect(self.visualItemRect(item)).contains(pos) else None

    def _hover(self, item, on_x=False):
        if (item, on_x) == (self.hovered, self.on_x):
            return
        self.hovered, self.on_x = item, on_x
        self.setCursor(Qt.PointingHandCursor if on_x else Qt.ArrowCursor)
        self.viewport().update()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if not event.buttons():
            self._hover(self.itemAt(pos), self._x_hit(pos) is not None)
        super().mouseMoveEvent(event)

    def viewportEvent(self, event):
        if event.type() == QEvent.Leave:
            self._hover(None)
        return super().viewportEvent(event)

    def mousePressEvent(self, event):
        hit = self._x_hit(event.position().toPoint())
        if hit is not None and event.button() == Qt.LeftButton:
            kind, name = hit.data(0, ROW)
            event.accept()                  # not a click on the row: it isn't opened or dragged
            QTimer.singleShot(0, lambda: self.delete_requested.emit(kind, name))
            return
        self._press_x = event.position().x()
        super().mousePressEvent(event)

    def _on_arrow(self, item) -> bool:
        """Whether the click that's being handled went on the item's arrow, left of its name."""
        x, self._press_x = self._press_x, None
        return x is not None and x < self.visualItemRect(item).left()

    def on_click(self, item):
        kind, name = item.data(0, ROW)
        if kind == "section":
            item.setExpanded(not item.isExpanded())
        elif kind == "project":
            if not self._on_arrow(item):
                QTimer.singleShot(0, lambda: self.project_clicked.emit(name))
        elif kind == "new section":
            self.new_section()
        elif kind == "new project":
            QTimer.singleShot(0, lambda: self.new_project_requested.emit(""))
        # Deferred: opening a chat redraws this list, which must not happen inside the click.
        elif kind == "new chat":
            QTimer.singleShot(0, self.new_chat_clicked.emit)
        else:
            QTimer.singleShot(0, lambda: self.chat_clicked.emit(name))

    # ------------------------------------------------------------------
    # Dragging
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event):
        if event.source() is self:
            super().dragEnterEvent(event)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        super().dragMoveEvent(event)       # scrolls the list near its edges
        event.accept()

    def dropEvent(self, event):
        pos = event.position().toPoint()
        target = self.itemAt(pos)
        below = bool(target) and pos.y() > self.visualItemRect(target).center().y()
        # Ignored, so Qt doesn't remove the dragged row: the redraw puts it in its new place.
        event.setDropAction(Qt.IgnoreAction)
        event.accept()
        dragged = self.currentItem()
        if dragged is not None and dragged.flags() & Qt.ItemIsDragEnabled:
            self._drop(dragged.data(0, ROW), target, below)

    def _drop(self, dragged, target, below):
        kind, name = dragged
        target_kind, target_name = target.data(0, ROW) if target else ("", "")
        if kind == "chat" and (target_kind == "project"
                               or (target is not None and target.parent() is not None
                                   and target.parent().data(0, ROW)[0] == "project")):
            return                          # a project's chats come from its links, not from dragging
        while target is not None and target_kind in ("chat", "project") and target.parent() is not None:
            target = target.parent()       # a row in a section stands for its section
            target_kind, target_name = target.data(0, ROW)
        into = target_name if target_kind == "section" else None
        if kind == "chat":
            chat_sections.move_chat(self.sections, name, into)
        elif kind == "project":
            chat_sections.move_project(self.sections, name, into)
        elif not into:
            chat_sections.move_section(self.sections, name)
        else:
            names = [s["Name"] for s in self.sections if s["Name"] != name]
            if below:
                after = names.index(into) + 1 if into in names else len(names)
                into = names[after] if after < len(names) else None
            chat_sections.move_section(self.sections, name, into)
        QTimer.singleShot(0, self._changed)

    # ------------------------------------------------------------------
    # Right-click menu
    # ------------------------------------------------------------------

    def show_menu(self, pos):
        item = self.itemAt(pos)
        kind, name = item.data(0, ROW) if item else ("", "")
        menu = QMenu(self)
        menu.setStyleSheet("QMenu{background:rgb(45,45,45);color:white}")
        if kind == "section":
            menu.addAction("Rename section…", lambda: self.rename_section(name))
            menu.addAction("Delete section (keeps its chats)…", lambda: self.delete_requested.emit("section", name))
            menu.addAction("Start new project here…", lambda: self.new_project_requested.emit(name))
        elif kind == "project":
            menu.addAction("Open project chat", lambda: self.project_clicked.emit(name))
            if any(p == name and lead for p, lead, _ in self.project_groups()):
                menu.addAction("Start project chat fresh…", lambda: self.fresh_requested.emit(name))
            self._move_menu(menu, chat_sections.section_of_project(self.sections, name),
                            lambda to: self.move_project(name, to))
            menu.addAction("Delete project and its chats…", lambda: self.delete_requested.emit("project", name))
        elif kind == "chat":
            if item.parent() is None or item.parent().data(0, ROW)[0] != "project":
                current = chat_sections.section_of(self.sections, name)
                move = self._move_menu(menu, current, lambda to: self.move_chat(name, to))
                move.addAction("New section…", lambda: self.new_section(name))
            menu.addAction("Delete chat…", lambda: self.delete_requested.emit("chat", name))
        else:
            menu.addAction("New section…", self.new_section)
            menu.addAction("Start new project…", lambda: self.new_project_requested.emit(""))
        if not menu.isEmpty():
            menu.exec(self.viewport().mapToGlobal(pos))

    def _move_menu(self, menu, current, move_to):
        """"Move to section" and, when it's in one, "Remove from section"."""
        move = menu.addMenu("Move to section")
        for s in self.sections:
            if s["Name"] != current:
                move.addAction(s["Name"], lambda to=s["Name"]: move_to(to))
        if current:
            menu.addAction("Remove from section", lambda: move_to(None))
        return move

    def _ask_name(self, title, text=""):
        name, ok = QInputDialog.getText(self, title, "Section name", text=text)
        return name.strip() if ok else ""

    def new_section(self, chat=None):
        """Ask for a name and add the section; `chat` goes straight into it."""
        name = self._ask_name("New section")
        if name and chat_sections.add(self.sections, name):
            if chat:
                chat_sections.move_chat(self.sections, chat, name)
            self._changed()

    def rename_section(self, name):
        new = self._ask_name("Rename section", name)
        if new and chat_sections.rename(self.sections, name, new):
            self._changed()

    def delete_section(self, name):
        chat_sections.remove(self.sections, name)
        self._changed()

    def move_chat(self, chat, to):
        chat_sections.move_chat(self.sections, chat, to)
        self._changed()

    def move_project(self, project, to):
        chat_sections.move_project(self.sections, project, to)
        self._changed()
