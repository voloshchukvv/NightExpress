"""Services: talking to the outside world (processes, Cargo, probe-rs, serial, files, settings).

Rule: services never import views, controllers or state models; they may only use
``models.entities``. No QtWidgets/QtGui.
"""
