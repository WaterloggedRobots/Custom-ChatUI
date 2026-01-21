from PySide6.QtWidgets import QMainWindow
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, Qt

class EmptyStart(QMainWindow):
    def __init__(self,pd, fd):
        super().__init__()
        self.ui = None
        self.directoryParent = pd
        self.directoryDefault = fd

        self.load_ui()
        self.setup_connections()
        # Store the initial stretch for the graphicsView column (e.g., 1)
        self.graphics_view_column_stretch = 1

    def load_ui(self):
        """Load the UI file"""
        try:
            ui_file = QFile(self.directoryDefault+r"\UI\Empty Start.ui")  # Change to your .ui file name
            if not ui_file.open(QFile.ReadOnly):
                print(f"Cannot open UI file: {ui_file.errorString()}")
                return

            loader = QUiLoader()
            self.ui = loader.load(ui_file)
            ui_file.close()

            if self.ui:
                self.setCentralWidget(self.ui)
                self.setWindowTitle("Warning: No Chats")

        except Exception as e:
            print(f"Error loading UI: {e}")

    def setup_connections(self):
        """Connect button signals to functions"""
        # Connect the toggle button - replace 'pushButton_7' with your actual button name
        #self.ui.pushButton.clicked.connect(self.newChatSettings)
        #self.ui.pushButton_2.clicked.connect(self.close())
        pass


class EmptyLeave(QMainWindow):
    def __init__(self, pd,fd):
        super().__init__()
        self.ui = None
        self.directoryParent = pd
        self.directoryDefault = fd

        self.load_ui()
        self.setup_connections()
        # Store the initial stretch for the graphicsView column (e.g., 1)
        self.graphics_view_column_stretch = 1

    def load_ui(self):
        """Load the UI file"""
        try:
            ui_file = QFile(self.directoryDefault+r"\UI\Warning Chat.ui")  # Change to your .ui file name
            if not ui_file.open(QFile.ReadOnly):
                print(f"Cannot open UI file: {ui_file.errorString()}")
                return

            loader = QUiLoader()
            self.ui = loader.load(ui_file)
            ui_file.close()

            if self.ui:
                self.setCentralWidget(self.ui)
                self.setWindowTitle("Warning: No Chats")

        except Exception as e:
            print(f"Error loading UI: {e}")

    def setup_connections(self):
        """Connect button signals to functions"""
        # Connect the toggle button - replace 'pushButton_7' with your actual button name
        self.ui.pushButton.clicked.connect(self.cancel)
        self.ui.pushButton_2.clicked.connect(self.exit)

    def cancel(self):
        self.close()

    def exit(self):
        global chatSettings
        chatSettings.close()
        self.close()

class InvalidChatSettings(QMainWindow):
    def __init__(self, pd, fd):
        super().__init__()
        self.ui = None
        self.directoryParent = pd
        self.directoryDefault = fd