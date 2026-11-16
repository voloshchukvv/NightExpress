"""ExamplesController: the "File → Examples" menu and creating a project from an example."""

from __future__ import annotations

from PySide6.QtCore import QObject

from nightexpress.controllers.project_controller import ProjectController
from nightexpress.models.entities import ProjectConfig
from nightexpress.models.state import ProjectModel
from nightexpress.services.board_catalog import BoardCatalog
from nightexpress.views.main_window import MainWindow


class ExamplesController(QObject):
    """Keeps the menu filtered by the open project's board; a click opens the new-project wizard.

    Without a project (or with a board missing from the catalog) every example is listed and
    the wizard narrows the choice once a board is picked.
    """

    def __init__(
        self,
        window: MainWindow,
        project_model: ProjectModel,
        catalog: BoardCatalog,
        projects: ProjectController,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._catalog = catalog
        window.example_requested.connect(projects.new_project)
        project_model.config_changed.connect(self._show_examples)
        self._show_examples(project_model.config)
        window.welcome.set_examples(catalog.examples)  # "Start from an example" cards

    def _show_examples(self, config: ProjectConfig | None) -> None:
        board = None
        if config is not None:
            try:
                board = self._catalog.get_board(config.board_id)
            except KeyError:
                board = None
        if board is None:
            self._window.set_examples(self._catalog.examples, None)
        else:
            self._window.set_examples(self._catalog.examples_for(board.id), board)
