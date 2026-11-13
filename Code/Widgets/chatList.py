"""The chat list on the left: sections you make and drag chats into (chat_sections.py),
and a group for each project holding its chats.

Dragging only says what moves where. The list is always redrawn from the
sections and the projects' links, so chats keep their most-recently-used order
inside each group. A project's chats can't be dragged out of it, but the project
can be dragged into a section. Clicking a project opens its own chat, where its
work is handed out; its arrow opens and closes the group.
"""

import chat_sections

from PySide6.QtWidgets import QInputDialog, QMenu, QTreeWidget, QTreeWidgetItem
from PySide6.QtCore import Qt, QTimer, Signal

NEW_CHAT_LABEL = "Create New Chat [+]"
NEW_PROJECT_LABEL = "Start New Project [+]"
NEW_SECTION_LABEL = "Create New Section [+]"
ROW = Qt.UserRole                       # each row's (kind, name); kind is "new chat", "new project", "new section",
                                        # "section", "project" or "chat"


class ChatList(QTreeWidget):
    chat_clicked = Signal(str)
    new_chat_clicked = Signal()
    project_clicked = Signal(str)       # open the project's own chat
    new_project_requested = Signal(str) # ask for a new project's name; it goes in this section ("" = none)
    sections_changed = Signal(list)     # the sections, after you changed them
    fresh_requested = Signal(str)       # a project whose own chat should start fresh

    def __init__(self, parent=None):
        super().__init__(parent)
        self.chats = []                 # chat names, oldest use first (.temp.json's ChatList)
        self.sections = []
        # [(project, its own chat or None, its other chats)], in the links' order (ChatMain sets it)
        self.project_groups = lambda: []
        self._closed_projects = set()
        self._press_x = None            # where the last click went down, to tell a project's arrow from its name
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

    def mousePressEvent(self, event):
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
            menu.addAction("Delete section (keeps its chats)", lambda: self.delete_section(name))
            menu.addAction("Start new project here…", lambda: self.new_project_requested.emit(name))
        elif kind == "project":
            menu.addAction("Open project chat", lambda: self.project_clicked.emit(name))
            if any(p == name and lead for p, lead, _ in self.project_groups()):
                menu.addAction("Start project chat fresh…", lambda: self.fresh_requested.emit(name))
            self._move_menu(menu, chat_sections.section_of_project(self.sections, name),
                            lambda to: self.move_project(name, to))
        elif kind == "chat" and (item.parent() is None or item.parent().data(0, ROW)[0] != "project"):
            current = chat_sections.section_of(self.sections, name)
            move = self._move_menu(menu, current, lambda to: self.move_chat(name, to))
            move.addAction("New section…", lambda: self.new_section(name))
        elif kind != "chat":
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
