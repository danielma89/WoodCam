"""Canvas vetorial 2D independente do Sketcher do FreeCAD."""

import math

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    try:
        from PySide2 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide import QtCore, QtGui
        QtWidgets = QtGui


class HitLineItem(QtWidgets.QGraphicsLineItem):
    """Linha com área de seleção maior que sua espessura visual."""

    def shape(self):
        path = QtGui.QPainterPath()
        path.moveTo(self.line().p1())
        path.lineTo(self.line().p2())
        stroker = QtGui.QPainterPathStroker()
        stroker.setWidth(10.0)
        return stroker.createStroke(path)


class VectorCanvas(QtWidgets.QGraphicsView):
    """Área milimétrica para vetores CAM, sem solver paramétrico."""

    def __init__(self, parent=None):
        super(VectorCanvas, self).__init__(parent)
        self.scene = QtWidgets.QGraphicsScene(self)
        self.setScene(self.scene)
        self.setSceneRect(-500.0, -500.0, 1000.0, 1000.0)
        self.setRenderHint(QtGui.QPainter.Antialiasing, True)
        self.setDragMode(QtWidgets.QGraphicsView.RubberBandDrag)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self._drawing = None
        self._area_item = None
        self._vector_items = []
        self._node_items = []
        self._active_node = None
        self._measure_item = None
        self._draw_grid()

    def _draw_grid(self):
        pen = QtGui.QPen(QtGui.QColor("#e5e7eb"), 0)
        for value in range(-500, 501, 10):
            self.scene.addLine(value, -500, value, 500, pen)
            self.scene.addLine(-500, value, 500, value, pen)
        axis_pen = QtGui.QPen(QtGui.QColor("#94a3b8"), 0)
        self.scene.addLine(-500, 0, 500, 0, axis_pen)
        self.scene.addLine(0, -500, 0, 500, axis_pen)

    def set_work_area(self, width, height):
        if self._area_item is not None:
            self.scene.removeItem(self._area_item)
        if width <= 0 or height <= 0:
            self._area_item = None
            return
        pen = QtGui.QPen(QtGui.QColor("#2563eb"), 0)
        pen.setStyle(QtCore.Qt.DashLine)
        self._area_item = self.scene.addRect(0, 0, width, -height, pen)

    def start_line(self):
        self._drawing = "line"
        self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
        self.setFocus()

    def start_circle(self):
        self._drawing = "circle"
        self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
        self.setFocus()

    def start_select(self):
        self._drawing = None
        self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
        self.setFocus()

    def delete_selected(self):
        for item in list(self.scene.selectedItems()):
            if item is self._area_item:
                continue
            self.scene.removeItem(item)
            if item in self._vector_items:
                self._vector_items.remove(item)
        self._clear_nodes()

    def _clear_nodes(self):
        for node in self._node_items:
            self.scene.removeItem(node)
        self._node_items = []

    def _show_measure(self, text, point):
        if self._measure_item is not None:
            self.scene.removeItem(self._measure_item)
        self._measure_item = self.scene.addSimpleText(text)
        self._measure_item.setBrush(QtGui.QBrush(QtGui.QColor("#b91c1c")))
        self._measure_item.setPos(point + QtCore.QPointF(6, -20))
        self._measure_item.setZValue(30)

    def _show_nodes(self, item):
        self._clear_nodes()
        points = []
        if isinstance(item, QtWidgets.QGraphicsLineItem):
            line = item.line()
            points = [line.p1(), line.p2()]
        elif isinstance(item, QtWidgets.QGraphicsEllipseItem):
            rect = item.rect()
            points = [rect.center(), QtCore.QPointF(rect.right(), rect.center().y())]
        for point in points:
            node = self.scene.addEllipse(
                point.x() - 5, point.y() - 5, 10, 10,
                QtGui.QPen(QtGui.QColor("#b91c1c"), 1),
                QtGui.QBrush(QtGui.QColor("#fecaca")),
            )
            node.setZValue(20)
            self._node_items.append(node)
        self._node_target = item

    def _update_active_node(self, point):
        item = getattr(self, "_node_target", None)
        if item is None or self._active_node is None:
            return
        node_index = self._active_node
        if isinstance(item, QtWidgets.QGraphicsLineItem):
            line = item.line()
            if node_index == 0:
                item.setLine(QtCore.QLineF(point, line.p2()))
            else:
                item.setLine(QtCore.QLineF(line.p1(), point))
            line = item.line()
            self._show_measure(f"{self._distance(line.p1(), line.p2()):g} mm", point)
        elif isinstance(item, QtWidgets.QGraphicsEllipseItem):
            rect = item.rect()
            if node_index == 0:
                delta = point - rect.center()
                item.setRect(rect.translated(delta))
            else:
                radius = max(1.0, abs(point.x() - rect.center().x()))
                item.setRect(
                    rect.center().x() - radius,
                    rect.center().y() - radius,
                    radius * 2,
                    radius * 2,
                )
            self._show_measure(f"Ø {item.rect().width():g} mm", point)
        self._show_nodes(item)

    @staticmethod
    def _distance(a, b):
        return math.hypot(a.x() - b.x(), a.y() - b.y())

    def snap_nearby_endpoints(self, tolerance):
        """Ajusta pontas de linhas ao vetor mais próximo sem solver externo."""
        changed = 0
        for item in list(self._vector_items):
            if not isinstance(item, QtWidgets.QGraphicsLineItem):
                continue
            line = item.line()
            endpoints = [line.p1(), line.p2()]
            snapped = list(endpoints)
            for endpoint_index, endpoint in enumerate(endpoints):
                best = None
                for other in self._vector_items:
                    if other is item:
                        continue
                    candidate = self._nearest_point_on_item(other, endpoint)
                    if candidate is None:
                        continue
                    distance = self._distance(endpoint, candidate)
                    if best is None or distance < best[0]:
                        best = (distance, candidate)
                if best is not None and best[0] <= tolerance:
                    snapped[endpoint_index] = best[1]
                    changed += 1
            item.setLine(QtCore.QLineF(snapped[0], snapped[1]))
        return changed

    def _nearest_point_on_item(self, item, point):
        if isinstance(item, QtWidgets.QGraphicsLineItem):
            line = item.line()
            dx = line.dx()
            dy = line.dy()
            length_sq = dx * dx + dy * dy
            if length_sq <= 1e-12:
                return line.p1()
            ratio = ((point.x() - line.x1()) * dx + (point.y() - line.y1()) * dy) / length_sq
            ratio = max(0.0, min(1.0, ratio))
            return QtCore.QPointF(line.x1() + ratio * dx, line.y1() + ratio * dy)
        if isinstance(item, QtWidgets.QGraphicsEllipseItem):
            rect = item.rect()
            center = rect.center()
            radius_x = rect.width() * 0.5
            radius_y = rect.height() * 0.5
            if radius_x <= 1e-9 or radius_y <= 1e-9:
                return None
            vx = point.x() - center.x()
            vy = point.y() - center.y()
            length = math.hypot(vx, vy)
            if length <= 1e-9:
                return QtCore.QPointF(center.x() + radius_x, center.y())
            scale = 1.0 / math.sqrt((vx / radius_x) ** 2 + (vy / radius_y) ** 2)
            return QtCore.QPointF(center.x() + vx * scale, center.y() + vy * scale)
        return None

    def clear_vectors(self):
        for item in list(self.scene.items()):
            if item is not self._area_item and item.parentItem() is None:
                self.scene.removeItem(item)
        self._vector_items = []
        self._draw_grid()

    def mousePressEvent(self, event):
        if self._drawing not in {"line", "circle"}:
            if self._drawing is None and event.button() == QtCore.Qt.LeftButton:
                item = self.itemAt(event.pos())
                if item in self._node_items:
                    item = getattr(self, "_node_target", None)
                    point = self.mapToScene(event.pos())
                    self._active_node = 0 if item is not None and self._distance(
                        point,
                        self._node_items[0].rect().center(),
                    ) < self._distance(point, self._node_items[-1].rect().center()) else 1
                if item in self._vector_items:
                    point = self.mapToScene(event.pos())
                    if isinstance(item, QtWidgets.QGraphicsLineItem):
                        line = item.line()
                        start_distance = self._distance(point, line.p1())
                        end_distance = self._distance(point, line.p2())
                        if min(start_distance, end_distance) > 12.0:
                            return super(VectorCanvas, self).mousePressEvent(event)
                        self._active_node = 0 if start_distance < end_distance else 1
                    else:
                        rect = item.rect()
                        center_distance = self._distance(point, rect.center())
                        edge_distance = abs(center_distance - rect.width() * 0.5)
                        if center_distance > 12.0 and edge_distance > 12.0:
                            return super(VectorCanvas, self).mousePressEvent(event)
                        self._active_node = 0 if center_distance < edge_distance else 1
                    self._show_nodes(item)
                    self._node_drag_start = point
                    event.accept()
                    return
                self._clear_nodes()
            return super(VectorCanvas, self).mousePressEvent(event)
        point = self.mapToScene(event.pos())
        self._start_point = point
        self._preview_item = None
        event.accept()

    def mouseMoveEvent(self, event):
        if self._active_node is not None:
            self._update_active_node(self.mapToScene(event.pos()))
            event.accept()
            return
        if getattr(self, "_start_point", None) is None or self._drawing is None:
            return super(VectorCanvas, self).mouseMoveEvent(event)
        point = self.mapToScene(event.pos())
        pen = QtGui.QPen(QtGui.QColor("#dc2626"), 1.5)
        if self._preview_item is not None:
            self.scene.removeItem(self._preview_item)
        if self._drawing == "line":
            self._preview_item = HitLineItem(
                QtCore.QLineF(self._start_point, point)
            )
            self._preview_item.setPen(pen)
            self.scene.addItem(self._preview_item)
        else:
            radius = ((point.x() - self._start_point.x()) ** 2 + (point.y() - self._start_point.y()) ** 2) ** 0.5
            self._preview_item = self.scene.addEllipse(
                self._start_point.x() - radius,
                self._start_point.y() - radius,
                radius * 2,
                radius * 2,
                pen,
            )
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._active_node is not None:
            self._active_node = None
            if self._measure_item is not None:
                self.scene.removeItem(self._measure_item)
                self._measure_item = None
            event.accept()
            return
        if getattr(self, "_start_point", None) is None:
            return super(VectorCanvas, self).mouseReleaseEvent(event)
        drawing_mode = self._drawing
        self._start_point = None
        if self._preview_item is not None:
            self._preview_item.setPen(QtGui.QPen(QtGui.QColor("#2563eb"), 1.5))
            self._preview_item.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, True)
            self._preview_item.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, True)
            self._preview_item.setData(0, drawing_mode)
            self._vector_items.append(self._preview_item)
        self._preview_item = None
        self._drawing = None
        self.setDragMode(QtWidgets.QGraphicsView.RubberBandDrag)
        event.accept()

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            self.delete_selected()
            event.accept()
            return
        super(VectorCanvas, self).keyPressEvent(event)
