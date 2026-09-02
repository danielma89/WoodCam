import os
import time

try:
    import FreeCAD as App
except ImportError:
    App = None

try:
    import FreeCADGui as Gui
except ImportError:
    Gui = None

try:
    import Part
except ImportError:
    Part = None

from .constants import (
    WORKBENCH_ID,
    PART_PROPERTY_GROUP,
    INTERNAL_PROPERTY_NAME,
    CONFIG_OBJECT_NAME,
)


def get_module_path():
    return os.path.dirname(os.path.dirname(__file__))


def get_icon_path(filename):
    return os.path.join(get_module_path(), "resources", "icons", filename)


def ensure_document():
    if App is None:
        raise RuntimeError("This module must run inside FreeCAD.")

    return App.ActiveDocument or App.newDocument(WORKBENCH_ID)


def create_demo_panel():
    doc = ensure_document()
    box = doc.addObject("Part::Box", "DemoPanel")
    box.Length = 600
    box.Width = 300
    box.Height = 18
    doc.recompute()
    return box


def _has_property(obj, property_name):
    return property_name in getattr(obj, "PropertiesList", [])


def _ensure_float_property_with_default(obj, property_name, description, default_value):
    if not _has_property(obj, property_name):
        obj.addProperty("App::PropertyFloat", property_name, PART_PROPERTY_GROUP, description)
        setattr(obj, property_name, float(default_value))


def _ensure_int_property_with_default(obj, property_name, description, default_value):
    if not _has_property(obj, property_name):
        obj.addProperty("App::PropertyInteger", property_name, PART_PROPERTY_GROUP, description)
        setattr(obj, property_name, int(default_value))


def _ensure_bool_property_with_default(obj, property_name, description, default_value):
    if not _has_property(obj, property_name):
        obj.addProperty("App::PropertyBool", property_name, PART_PROPERTY_GROUP, description)
        setattr(obj, property_name, bool(default_value))


def _ensure_string_property_with_default(obj, property_name, description, default_value):
    if not _has_property(obj, property_name):
        obj.addProperty("App::PropertyString", property_name, PART_PROPERTY_GROUP, description)
        setattr(obj, property_name, str(default_value))


def _ensure_string_property(obj, property_name, description):
    if not _has_property(obj, property_name):
        obj.addProperty("App::PropertyString", property_name, PART_PROPERTY_GROUP, description)


def _ensure_float_property(obj, property_name, description):
    if not _has_property(obj, property_name):
        obj.addProperty("App::PropertyFloat", property_name, PART_PROPERTY_GROUP, description)


def _ensure_bool_property(obj, property_name, description):
    if not _has_property(obj, property_name):
        obj.addProperty("App::PropertyBool", property_name, PART_PROPERTY_GROUP, description)


def _ensure_enum_property(obj, property_name, description, values, default_value):
    if not _has_property(obj, property_name):
        obj.addProperty("App::PropertyEnumeration", property_name, PART_PROPERTY_GROUP, description)

    current_value = getattr(obj, property_name, default_value)
    setattr(obj, property_name, values)
    setattr(obj, property_name, current_value if current_value in values else default_value)


def _mark_internal_object(obj, internal_type):
    if not _has_property(obj, INTERNAL_PROPERTY_NAME):
        obj.addProperty(
            "App::PropertyString",
            INTERNAL_PROPERTY_NAME,
            PART_PROPERTY_GROUP,
            "Uso interno do PanelNest",
        )
    setattr(obj, INTERNAL_PROPERTY_NAME, internal_type)


def _is_internal_object(obj):
    return _has_property(obj, INTERNAL_PROPERTY_NAME) and bool(
        getattr(obj, INTERNAL_PROPERTY_NAME, "")
    )


def _remove_object_tree(doc, obj):
    for child in list(getattr(obj, "Group", [])):
        _remove_object_tree(doc, child)
    if doc.getObject(obj.Name) is not None:
        doc.removeObject(obj.Name)


def _safe_gui_refresh():
    if Gui is None:
        return
    updater = getattr(Gui, "updateGui", None)
    if callable(updater):
        try:
            updater()
        except Exception:
            pass
    for module_name in ("PySide", "PySide2", "PySide6"):
        try:
            qt_core = __import__(module_name, fromlist=["QtCore"]).QtCore
            app_instance = getattr(qt_core, "QCoreApplication", None)
            if app_instance is not None and hasattr(app_instance, "processEvents"):
                try:
                    app_instance.processEvents()
                except Exception:
                    pass
            break
        except Exception:
            continue


def _qt_modules():
    for module_name in ("PySide", "PySide2", "PySide6"):
        try:
            module = __import__(module_name, fromlist=["QtCore", "QtGui", "QtWidgets"])
            return (
                getattr(module, "QtCore", None),
                getattr(module, "QtGui", None),
                getattr(module, "QtWidgets", None),
            )
        except Exception:
            continue
    return (None, None, None)


def _active_view_for_document(document=None):
    if Gui is None:
        return None

    doc = document or ensure_document()
    gui_doc = None
    try:
        gui_doc = Gui.getDocument(getattr(doc, "Name", ""))
    except Exception:
        gui_doc = None
    if gui_doc is None:
        gui_doc = getattr(Gui, "ActiveDocument", None)
    if gui_doc is None:
        return None

    active_view = getattr(gui_doc, "ActiveView", None)
    if active_view is not None:
        return active_view

    getter = getattr(gui_doc, "activeView", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            return None
    return None


def _active_view_widget():
    if Gui is None:
        return None

    _qt_core, _qt_gui, qt_widgets = _qt_modules()
    if qt_widgets is None:
        return None

    main_window_getter = getattr(Gui, "getMainWindow", None)
    if not callable(main_window_getter):
        return None

    try:
        main_window = main_window_getter()
    except Exception:
        return None
    if main_window is None:
        return None

    mdi_area = None
    mdi_area_type = getattr(qt_widgets, "QMdiArea", None)
    if mdi_area_type is not None and hasattr(main_window, "findChild"):
        try:
            mdi_area = main_window.findChild(mdi_area_type)
        except Exception:
            mdi_area = None
    if mdi_area is None:
        return None

    sub_window = None
    try:
        sub_window = mdi_area.activeSubWindow()
    except Exception:
        sub_window = None
    if sub_window is None:
        try:
            sub_windows = list(mdi_area.subWindowList() or [])
        except Exception:
            sub_windows = []
        for candidate in sub_windows:
            if candidate is None:
                continue
            try:
                if candidate.isVisible():
                    sub_window = candidate
                    break
            except Exception:
                continue
        if sub_window is None and sub_windows:
            sub_window = sub_windows[0]

    if sub_window is None:
        return None

    widget = None
    try:
        widget = sub_window.widget()
    except Exception:
        widget = None
    if widget is None:
        return None

    best_widget = widget
    best_score = -1

    candidates = [widget]
    if hasattr(widget, "findChildren") and qt_widgets is not None and hasattr(qt_widgets, "QWidget"):
        try:
            candidates.extend(list(widget.findChildren(qt_widgets.QWidget)))
        except Exception:
            pass

    for candidate in candidates:
        if candidate is None:
            continue
        try:
            if hasattr(candidate, "isVisible") and not candidate.isVisible():
                continue
        except Exception:
            continue
        class_name = ""
        try:
            meta_object = candidate.metaObject() if hasattr(candidate, "metaObject") else None
            class_name = str(meta_object.className() if meta_object is not None else "") or ""
        except Exception:
            class_name = ""
        score = 0
        lowered = class_name.lower()
        if hasattr(candidate, "grabFramebuffer"):
            score += 1000
        if "opengl" in lowered or "gl" in lowered:
            score += 500
        if "quarter" in lowered or "view3d" in lowered or "inventor" in lowered:
            score += 400
        try:
            score += int(max(0, candidate.width()) * max(0, candidate.height()) / 1000)
        except Exception:
            pass
        if score > best_score:
            best_score = score
            best_widget = candidate

    return best_widget


def _save_active_view_widget_snapshot(image_path, width_px=None, height_px=None):
    _qt_core, _qt_gui, _qt_widgets = _qt_modules()
    if _qt_gui is None:
        return False

    widget = _active_view_widget()
    if widget is None or not hasattr(widget, "grab"):
        return False

    active_view = None
    try:
        active_view = getattr(getattr(Gui, "ActiveDocument", None), "ActiveView", None) if Gui is not None else None
    except Exception:
        active_view = None

    view_preferences = None
    original_show_navicube = None
    original_show_rotation_center = None
    original_axis_cross = None
    try:
        if App is not None and hasattr(App, "ParamGet"):
            view_preferences = App.ParamGet("User parameter:BaseApp/Preferences/View")
            original_show_navicube = bool(view_preferences.GetBool("ShowNaviCube", True))
            original_show_rotation_center = bool(view_preferences.GetBool("ShowRotationCenter", True))
            view_preferences.SetBool("ShowNaviCube", False)
            view_preferences.SetBool("ShowRotationCenter", False)
    except Exception:
        view_preferences = None
        original_show_navicube = None
        original_show_rotation_center = None

    try:
        if (
            active_view is not None
            and hasattr(active_view, "hasAxisCross")
            and hasattr(active_view, "setAxisCross")
        ):
            original_axis_cross = bool(active_view.hasAxisCross())
            active_view.setAxisCross(False)
    except Exception:
        original_axis_cross = None

    def _warm_up_widget_snapshot():
        try:
            if hasattr(widget, "update"):
                widget.update()
            if hasattr(widget, "repaint"):
                widget.repaint()
        except Exception:
            pass

        _safe_gui_refresh()
        time.sleep(0.08)

        try:
            if hasattr(widget, "grabFramebuffer"):
                warmed = widget.grabFramebuffer()
                if warmed is not None:
                    pass
        except Exception:
            pass

        try:
            warmed_pixmap = widget.grab()
            if warmed_pixmap is not None:
                pass
        except Exception:
            pass

        _safe_gui_refresh()
        time.sleep(0.03)

    warmed_widgets = getattr(_save_active_view_widget_snapshot, "_warmed_widgets", None)
    if not isinstance(warmed_widgets, set):
        warmed_widgets = set()
        setattr(_save_active_view_widget_snapshot, "_warmed_widgets", warmed_widgets)

    widget_key = int(id(widget))

    _safe_gui_refresh()
    if widget_key not in warmed_widgets:
        _warm_up_widget_snapshot()
        warmed_widgets.add(widget_key)
    else:
        time.sleep(0.03)

    target_width = max(1, int(width_px or 0))
    target_height = max(1, int(height_px or 0))
    aspect_mode = getattr(_qt_core.Qt, "KeepAspectRatio", None) if _qt_core else None
    transform_mode = getattr(_qt_core.Qt, "SmoothTransformation", None) if _qt_core else None

    def _sample_image_color(image, x, y, fallback):
        try:
            clamped_x = max(0, min(int(x), int(image.width()) - 1))
            clamped_y = max(0, min(int(y), int(image.height()) - 1))
            if hasattr(image, "pixelColor"):
                return image.pixelColor(clamped_x, clamped_y)
        except Exception:
            pass
        return fallback

    def _mask_viewport_ornaments(image):
        if image is None or not hasattr(image, "width") or not hasattr(image, "height"):
            return image

        try:
            width = int(image.width())
            height = int(image.height())
        except Exception:
            return image
        if width <= 0 or height <= 0:
            return image

        if hasattr(image, "convertToFormat") and hasattr(_qt_gui, "QImage"):
            try:
                image = image.convertToFormat(_qt_gui.QImage.Format_ARGB32)
            except Exception:
                pass

        if not hasattr(_qt_gui, "QPainter") or not hasattr(_qt_core, "QRectF"):
            return image

        top_size = max(92, min(228, int(min(width, height) * 0.29)))
        top_margin = max(10, int(top_size * 0.12))
        axis_size = max(32, min(78, int(min(width, height) * 0.09)))
        axis_margin = max(8, int(axis_size * 0.34))

        masks = [
            _qt_core.QRectF(
                max(0, width - int(top_size * 1.34) - top_margin),
                max(0.0, float(top_margin) * 0.20),
                float(top_size * 1.34),
                float(top_size * 1.04),
            ),
            _qt_core.QRectF(
                max(0, width - int(top_size * 0.92) - int(top_margin * 0.45)),
                max(0.0, float(top_margin) * 0.05),
                float(top_size * 0.96),
                float(top_size * 1.16),
            ),
            _qt_core.QRectF(
                max(0, width - axis_size - axis_margin),
                max(0, height - axis_size - axis_margin),
                float(axis_size),
                float(axis_size),
            ),
        ]

        fallback_color = getattr(_qt_gui, "QColor", lambda *_args: None)(245, 245, 245, 255)
        painter = _qt_gui.QPainter(image)
        try:
            if hasattr(painter, "setRenderHint") and hasattr(_qt_gui, "QPainter"):
                try:
                    painter.setRenderHint(_qt_gui.QPainter.Antialiasing, True)
                except Exception:
                    pass
            if hasattr(_qt_gui.QPainter, "CompositionMode_Source") and hasattr(painter, "setCompositionMode"):
                try:
                    painter.setCompositionMode(_qt_gui.QPainter.CompositionMode_Source)
                except Exception:
                    pass
            if hasattr(_qt_core.Qt, "NoPen"):
                painter.setPen(_qt_core.Qt.NoPen)

            for rect in masks:
                if rect.width() <= 0 or rect.height() <= 0:
                    continue
                sample_x = max(0, int(rect.left()) - 24)
                top_color = _sample_image_color(
                    image,
                    sample_x,
                    int(rect.top()) + 2,
                    fallback_color,
                )
                bottom_color = _sample_image_color(
                    image,
                    sample_x,
                    int(rect.bottom()) - 2,
                    top_color,
                )
                if hasattr(_qt_gui, "QLinearGradient"):
                    gradient = _qt_gui.QLinearGradient(rect.topLeft(), rect.bottomLeft())
                    gradient.setColorAt(0.0, top_color)
                    gradient.setColorAt(1.0, bottom_color)
                    painter.setBrush(_qt_gui.QBrush(gradient))
                else:
                    painter.setBrush(top_color)
                radius = max(5.0, min(rect.width(), rect.height()) * 0.22)
                painter.drawRoundedRect(rect, radius, radius)
        finally:
            painter.end()

        return image

    try:
        image = None
        if hasattr(widget, "grabFramebuffer"):
            try:
                image = widget.grabFramebuffer()
            except Exception:
                image = None
            if image is not None and hasattr(image, "isNull") and image.isNull():
                image = None

        if image is not None:
            if target_width > 0 and target_height > 0 and hasattr(image, "scaled"):
                try:
                    if aspect_mode is not None and transform_mode is not None:
                        image = image.scaled(target_width, target_height, aspect_mode, transform_mode)
                except Exception:
                    pass
            image = _mask_viewport_ornaments(image)
            try:
                return bool(image.save(image_path, "PNG"))
            except Exception:
                return False

        try:
            pixmap = widget.grab()
        except Exception:
            return False
        if pixmap is None or pixmap.isNull():
            return False

        if target_width > 0 and target_height > 0 and hasattr(pixmap, "scaled"):
            try:
                if aspect_mode is not None and transform_mode is not None:
                    pixmap = pixmap.scaled(target_width, target_height, aspect_mode, transform_mode)
            except Exception:
                pass

        try:
            pixmap_image = pixmap.toImage() if hasattr(pixmap, "toImage") else None
            pixmap_image = _mask_viewport_ornaments(pixmap_image)
            if pixmap_image is not None:
                return bool(pixmap_image.save(image_path, "PNG"))
        except Exception:
            pass

        try:
            return bool(pixmap.save(image_path, "PNG"))
        except Exception:
            return False
    finally:
        try:
            if original_axis_cross is not None and active_view is not None and hasattr(active_view, "setAxisCross"):
                active_view.setAxisCross(bool(original_axis_cross))
        except Exception:
            pass
        try:
            if view_preferences is not None and original_show_navicube is not None:
                view_preferences.SetBool("ShowNaviCube", bool(original_show_navicube))
            if view_preferences is not None and original_show_rotation_center is not None:
                view_preferences.SetBool("ShowRotationCenter", bool(original_show_rotation_center))
        except Exception:
            pass
        _safe_gui_refresh()


def _add_object_to_group(group, obj):
    proxy = getattr(group, "Proxy", None)
    if proxy is not None and hasattr(proxy, "addObject"):
        try:
            proxy.addObject(group, obj)
            return
        except Exception:
            pass
    add_object = getattr(group, "addObject", None)
    if add_object is not None:
        try:
            add_object(obj)
        except Exception:
            pass


def _group_member_count(group):
    return len(list(getattr(group, "Group", []) or []))
