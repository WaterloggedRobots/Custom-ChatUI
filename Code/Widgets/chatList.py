"""The chat list on the left: sections you make and drag chats into (chat_sections.py).

Dragging only says what moves where. The list is always redrawn from the
sections, so chats keep their most-recently-used order inside each section.
"""

import chat_sections

from PySide6.QtWidgets import QInputDialog, QMenu, QTreeWidget, QTreeWidgetItem
from PySide6.QtCore import Qt, QTimer, Signal

NEW_CHAT_LABEL = "Create New Chat [+]"
NEW_SECTION_LABEL = "Create New Section [+]"
ROW = Qt.UserRole                       # each row's (kind, name); kind is "new chat", "new section", "section" or "chat"


class ChatList(QTreeWidget):
    chat_clicked = Signal(str)
    new_chat_clicked = Signal()
    sections_changed = Signal(list)     # the sections, after you changed them

    def __init__(self, parent=None):
        super().__init__(parent)
        self.chats = []                 # chat names, oldest use first (.temp.json's ChatList)
        self.sections = []
        self.project_of = lambda chat: None     # the project a chat is the own chat of (ChatMain sets it)
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
        self._row(self, "new chat", "", NEW_CHAT_LABEL, Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        self._row(self, "new section", "", NEW_SECTION_LABEL, Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        chat_flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled
        grouped = set()
        for s in self.sections:
            members = [c for c in reversed(self.chats) if c in s["Chats"]]
            grouped.update(members)
            item = self._row(self, "section", s["Name"], f"{s['Name']} ({len(members)})", chat_flags)
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            for chat in members:
                self._chat_row(item, chat, chat_flags)
            item.setExpanded(s["Open"])
        for chat in reversed(self.chats):
            if chat not in grouped:
                self._chat_row(self, chat, chat_flags)
        self.blockSignals(False)
        self.verticalScrollBar().setValue(pos)

    def _chat_row(self, parent, chat, flags):
        project = self.project_of(chat)
        item = self._row(parent, "chat", chat, f"📂 {chat}" if project else chat, flags)
        if project:
            item.setToolTip(0, f"The own chat of project {project}: it hands out the project's work")
        return item

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
        section = chat_sections.find(self.sections, name) if kind == "section" else None
        if section:
            section["Open"] = is_open
            self.sections_changed.emit(self.sections)

    def on_click(self, item):
        kind, name = item.data(0, ROW)
        if kind == "section":
            item.setExpanded(not item.isExpanded())
        elif kind == "new section":
            self.new_section()
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
        if target_kind == "chat":
            parent = target.parent()       # a chat in a section stands for its section
            target_kind, target_name = parent.data(0, ROW) if parent else ("", "")
        into = target_name if target_kind == "section" else None
        if kind == "chat":
            chat_sections.move_chat(self.sections, name, into)
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
        elif kind == "chat":
            move = menu.addMenu("Move to section")
            current = chat_sections.section_of(self.sections, name)
            for s in self.sections:
                if s["Name"] != current:
                    move.addAction(s["Name"], lambda to=s["Name"]: self.move_chat(name, to))
            move.addAction("New section…", lambda: self.new_section(name))
            if current:
                menu.addAction("Remove from section", lambda: self.move_chat(name, None))
        else:
            menu.addAction("New section…", self.new_section)
        menu.exec(self.viewport().mapToGlobal(pos))

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
