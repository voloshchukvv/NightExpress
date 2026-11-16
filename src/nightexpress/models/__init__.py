"""Model: application data and state.

- ``entities``: plain dataclasses without Qt (boards, chips, project config, diagnostics).
- ``state``: observable models (QObject + Signal), QtCore only.
- ``qt_models``: adapters for Qt Model/View (QAbstractItemModel).

Rule: models never import views, controllers or services.
"""
