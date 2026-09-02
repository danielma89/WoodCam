"""FreeCADCmd regression for an empty Editor 2D mouse path.

The canvas must remain responsive before any geometry is loaded. Pan and
construction previews are rendered at frame cadence while their final click
continues to use the exact pointer coordinate.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from PySide6 import QtCore, QtTest, QtWidgets  # noqa: E402

from woodcam_editor.application import EditorMode  # noqa: E402
from woodcam_editor.domain import VectorDocument  # noqa: E402
from woodcam_editor.presentation.widget import Editor2DWidget  # noqa: E402


app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
widget = Editor2DWidget(VectorDocument.create_default())
widget.resize(1200, 820)
widget.show()
app.processEvents()
viewport = widget.view.viewport()

feedback = []
widget.view.cursorMoved.connect(feedback.append)
started = time.perf_counter()
for index in range(600):
    x = 2 + (index * 17) % max(1, viewport.width() - 4)
    y = 2 + (index * 29) % max(1, viewport.height() - 4)
    QtTest.QTest.mouseMove(viewport, QtCore.QPoint(x, y), -1)
    if index % 20 == 0:
        app.processEvents()
app.processEvents()
elapsed = time.perf_counter() - started
widget.view._cursor_display_timer.stop()
widget.view._flush_cursor_display()

# The reference environment completes this path near 0.03 s. Keep a broad
# margin for shared/slow CI while still catching the old 1,200 synchronous
# ruler paints, which exceeded 0.26 s on the same machine.
assert elapsed < 0.18, "empty editor mouse path too slow: %.6f s" % elapsed
assert len(feedback) < 100, "visual feedback was not coalesced: %d" % len(feedback)
assert not widget.controller.document.entities_by_id

# Middle-button pan used to grow sceneRect and set two scroll bars for every
# input packet. Accumulate packets, render one frame and keep a broad CI margin.
pan_started = time.perf_counter()
pan_start = QtCore.QPoint(400, 300)
QtTest.QTest.mousePress(
    viewport, QtCore.Qt.MiddleButton, QtCore.Qt.NoModifier, pan_start
)
for index in range(600):
    pan_point = QtCore.QPoint(400 + index % 180, 300 + (index * 3) % 120)
    QtTest.QTest.mouseMove(viewport, pan_point, -1)
    if index % 20 == 0:
        app.processEvents()
QtTest.QTest.mouseRelease(
    viewport, QtCore.Qt.MiddleButton, QtCore.Qt.NoModifier, pan_point
)
app.processEvents()
pan_elapsed = time.perf_counter() - pan_started
assert pan_elapsed < 0.10, "middle-button pan too slow: %.6f s" % pan_elapsed

# Rectangle preview previously rebuilt/pained its path and measurement bubble
# for every mouse packet. Confirm both cadence and the exact final click.
widget.activate_tool(EditorMode.DRAW_RECTANGLE)
first_screen = QtCore.QPoint(220, 220)
first_scene = widget.view.mapToScene(first_screen)
QtTest.QTest.mouseClick(
    viewport,
    QtCore.Qt.LeftButton,
    QtCore.Qt.ShiftModifier,
    first_screen,
)
preview_started = time.perf_counter()
for index in range(600):
    preview_point = QtCore.QPoint(
        240 + index % 500,
        240 + (index * 3) % 360,
    )
    QtTest.QTest.mouseMove(viewport, preview_point, -1)
    if index % 20 == 0:
        app.processEvents()
app.processEvents()
preview_elapsed = time.perf_counter() - preview_started
assert preview_elapsed < 0.14, "rectangle preview too slow: %.6f s" % preview_elapsed

final_screen = QtCore.QPoint(760, 610)
final_scene = widget.view.mapToScene(final_screen)
QtTest.QTest.mouseClick(
    viewport,
    QtCore.Qt.LeftButton,
    QtCore.Qt.ShiftModifier,
    final_screen,
)
app.processEvents()
entities = tuple(widget.controller.document.entities_by_id.values())
assert len(entities) == 1
bounds = entities[0].bounds()
assert abs(bounds.min_x - min(first_scene.x(), final_scene.x())) < 1.0e-6
assert abs(bounds.max_x - max(first_scene.x(), final_scene.x())) < 1.0e-6
assert abs(bounds.min_y - min(first_scene.y(), final_scene.y())) < 1.0e-6
assert abs(bounds.max_y - max(first_scene.y(), final_scene.y())) < 1.0e-6

assert widget.sheet_panel._collapse_button.text() == "▼"
widget.sheet_panel._collapse_button.click()
app.processEvents()
assert widget.sheet_panel._collapse_button.text() == "▶"

print(
    "WoodCAM empty Editor 2D responsiveness smoke: OK "
    "(hover %.6f s; pan %.6f s; rectangle %.6f s)"
    % (elapsed, pan_elapsed, preview_elapsed)
)
widget.close()
app.processEvents()
