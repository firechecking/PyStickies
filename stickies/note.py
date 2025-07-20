from PyQt5.QtWidgets import QMainWindow


class Note(QMainWindow):
    def __init__(self, note_id, manager):
        super().__init__()
        self.note_id = note_id
        self.manager = manager
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle(self.note_id)
        self.setGeometry(100, 100, 100, 100)

    def from_json(self, note_id, manager):
        self.note_id = note_id
        self.manager = manager
        self.init_ui()

    def to_json(self, note_id, manager):
        pass

    def show(self):
        pass
