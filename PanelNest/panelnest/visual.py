import base64
import os
import re
import time
import shutil
import tempfile
import unicodedata

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
    LAYOUT_ROOT_NAME,
    INTERNAL_PROPERTY_NAME,
    PART_PROPERTY_GROUP,
    ASSEMBLY_GUIDE_CONTEXT_COLOR,
    ASSEMBLY_GUIDE_TARGET_COLOR,
    ASSEMBLY_GUIDE_CONTEXT_LINE_COLOR,
    ASSEMBLY_GUIDE_TARGET_LINE_COLOR,
    ASSEMBLY_GUIDE_CONTEXT_LINE_WIDTH,
    ASSEMBLY_GUIDE_TARGET_LINE_WIDTH,
    ASSEMBLY_GUIDE_CONTEXT_TRANSPARENCY,
    ASSEMBLY_GUIDE_TARGET_TRANSPARENCY,
    ASSEMBLY_GUIDE_MANUAL_TARGET_TRANSPARENCY,
    ASSEMBLY_GUIDE_IMAGE_WIDTH_PX,
    ASSEMBLY_GUIDE_IMAGE_HEIGHT_PX,
)
from .freecad_utils import (
    _has_property,
    _is_internal_object,
    ensure_document,
    _safe_gui_refresh,
    _qt_modules,
    _active_view_for_document,
    _active_view_widget,
    _save_active_view_widget_snapshot,
)
from .geometry import (
    _is_candidate_object,
    _shape_solid_list,
    _solid_contexts_for_shape,
    _solid_context_for_face,
)
from .edge_band import _normalize_color_triplet, _current_view_shape_color, _current_view_diffuse_colors
from .models import SheetSettings


def _lazy_collect_part_label_records(parts=None, document=None):
    from .spreadsheets import collect_part_label_records
    return collect_part_label_records(parts=parts, document=document)


def _lazy_get_sheet_settings():
    from .metadata import get_sheet_settings
    return get_sheet_settings()


def _lazy_is_user_project_container(obj):
    from .parts import _is_user_project_container
    return _is_user_project_container(obj)


def _lazy_ancestor_user_project_containers(obj):
    from .parts import _ancestor_user_project_containers
    return _ancestor_user_project_containers(obj)

def _slugify_fragment(text, fallback="item"):
    normalized = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return slug or fallback


def _assembly_record_occurrence_text(record):
    occurrence = str((record or {}).get("occurrence", "") or "").strip()
    return occurrence or "01/01"


def _assembly_record_key(record):
    return "%s|%s" % (
        str((record or {}).get("part_id", "") or "").strip(),
        _assembly_record_occurrence_text(record),
    )


def _assembly_record_anchor(record):
    occurrence = _assembly_record_occurrence_text(record).replace("/", "-")
    return _slugify_fragment("%s-%s" % (str((record or {}).get("part_id", "") or "peca"), occurrence), "peca")


def _parse_occurrence_object_name(object_name):
    raw_name = str(object_name or "").strip()
    if "#" not in raw_name:
        return raw_name, 1
    base_name, raw_occurrence = raw_name.rsplit("#", 1)
    try:
        occurrence_index = int(raw_occurrence)
    except Exception:
        occurrence_index = 1
    return base_name or raw_name, max(1, occurrence_index)




def _capture_gui_selection_state(document=None):
    if Gui is None:
        return []

    selection = getattr(Gui, "Selection", None)
    if selection is None:
        return []

    document_name = getattr(document, "Name", "") if document is not None else ""
    selection_ex = []
    getter = getattr(selection, "getSelectionEx", None)
    if callable(getter):
        try:
            selection_ex = getter(document_name, 0) if document_name else getter()
        except TypeError:
            try:
                selection_ex = getter(document_name) if document_name else getter()
            except Exception:
                selection_ex = []
        except Exception:
            selection_ex = []

    captured = []
    seen = set()
    for item in selection_ex or []:
        raw_object = getattr(item, "Object", None)
        object_name = getattr(raw_object, "Name", "") or ""
        if not object_name:
            continue

        sub_names = list(getattr(item, "SubElementNames", []) or [])
        if not sub_names:
            entry = (object_name, "")
            if entry not in seen:
                seen.add(entry)
                captured.append(entry)
            continue

        for sub_name in sub_names:
            entry = (object_name, str(sub_name or ""))
            if entry in seen:
                continue
            seen.add(entry)
            captured.append(entry)

    return captured


def _clear_gui_selection():
    if Gui is None:
        return

    selection = getattr(Gui, "Selection", None)
    if selection is None or not hasattr(selection, "clearSelection"):
        return
    try:
        selection.clearSelection()
    except Exception:
        pass


def _restore_gui_selection_state(selection_state, document=None):
    if Gui is None:
        return

    selection = getattr(Gui, "Selection", None)
    if selection is None:
        return

    _clear_gui_selection()

    add_selection = getattr(selection, "addSelection", None)
    if not callable(add_selection):
        return

    doc = document or getattr(App, "ActiveDocument", None)
    document_name = getattr(doc, "Name", "") if doc is not None else ""
    target_doc = doc if document_name else None
    for object_name, sub_name in selection_state or []:
        target_obj = target_doc.getObject(object_name) if target_doc is not None else None
        if target_doc is not None and target_obj is None:
            continue

        restored = False
        if document_name and sub_name:
            try:
                add_selection(document_name, object_name, sub_name)
                restored = True
            except Exception:
                restored = False
        if not restored and document_name:
            try:
                add_selection(document_name, object_name)
                restored = True
            except Exception:
                restored = False
        if not restored and target_obj is not None:
            try:
                add_selection(target_obj)
            except Exception:
                pass


def _select_gui_objects(objects, document=None):
    if Gui is None:
        return 0

    selection = getattr(Gui, "Selection", None)
    if selection is None:
        return 0

    add_selection = getattr(selection, "addSelection", None)
    if not callable(add_selection):
        return 0

    doc = document or getattr(App, "ActiveDocument", None)
    document_name = getattr(doc, "Name", "") if doc is not None else ""
    added_count = 0
    seen_names = set()
    for obj in objects or []:
        object_name = getattr(obj, "Name", "") or ""
        if not object_name or object_name in seen_names:
            continue
        seen_names.add(object_name)
        try:
            if document_name:
                add_selection(document_name, object_name)
            else:
                add_selection(obj)
            added_count += 1
        except Exception:
            continue
    return added_count


def _toggle_gui_transparency_for_objects(objects, document=None):
    if Gui is None or not objects:
        return False
    run_command = getattr(Gui, "runCommand", None)
    if not callable(run_command):
        return False

    previous_transparency = {
        getattr(obj, "Name", ""): int(getattr(getattr(obj, "ViewObject", None), "Transparency", 0) or 0)
        for obj in objects
        if getattr(obj, "ViewObject", None) is not None
    }

    _clear_gui_selection()
    selected_count = _select_gui_objects(objects, document=document)
    if selected_count <= 0:
        return False

    try:
        run_command("Std_ToggleTransparency", 0)
    except Exception:
        return False

    _safe_gui_refresh()
    _clear_gui_selection()

    for obj in objects:
        view_object = getattr(obj, "ViewObject", None)
        if view_object is None or not hasattr(view_object, "Transparency"):
            continue
        current_transparency = int(getattr(view_object, "Transparency", 0) or 0)
        if current_transparency != previous_transparency.get(getattr(obj, "Name", ""), current_transparency):
            return True
    return False


def _saved_preview_metrics(image_path):
    if not image_path or not os.path.exists(image_path):
        return None

    try:
        try:
            from PySide import QtGui  # type: ignore
        except ImportError:
            try:
                from PySide2 import QtGui  # type: ignore
            except ImportError:
                from PySide6 import QtGui  # type: ignore
    except Exception:
        return None

    image = QtGui.QImage(image_path)
    if image.isNull():
        return None

    width = int(image.width() or 0)
    height = int(image.height() or 0)
    if width <= 8 or height <= 8:
        return None

    def _pixel_rgba(x_pos, y_pos):
        try:
            color = image.pixelColor(x_pos, y_pos)
            return (
                int(color.red()),
                int(color.green()),
                int(color.blue()),
                int(color.alpha()),
            )
        except Exception:
            try:
                pixel_value = image.pixel(x_pos, y_pos)
                color = QtGui.QColor(pixel_value)
                return (
                    int(color.red()),
                    int(color.green()),
                    int(color.blue()),
                    int(color.alpha()),
                )
            except Exception:
                return (255, 255, 255, 255)

    edge_margin = min(6, max(1, min(width, height) // 20))
    background_samples = []
    sample_positions = (
        (edge_margin, edge_margin),
        (width - 1 - edge_margin, edge_margin),
        (edge_margin, height - 1 - edge_margin),
        (width - 1 - edge_margin, height - 1 - edge_margin),
        (width // 2, edge_margin),
        (width // 2, height - 1 - edge_margin),
        (edge_margin, height // 2),
        (width - 1 - edge_margin, height // 2),
    )
    for sample_x, sample_y in sample_positions:
        background_samples.append(_pixel_rgba(sample_x, sample_y))

    if not background_samples:
        return None

    background_rgba = tuple(
        int(round(sum(sample[channel_index] for sample in background_samples) / len(background_samples)))
        for channel_index in range(4)
    )

    color_threshold = 10
    alpha_threshold = 18
    found_pixels = 0
    min_x = width
    min_y = height
    max_x = -1
    max_y = -1
    sampling_step = 2 if max(width, height) > 600 else 1
    required_pixels = max(12, int((width * height) / 25000))
    for y_pos in range(0, height, sampling_step):
        for x_pos in range(0, width, sampling_step):
            pixel_rgba = _pixel_rgba(x_pos, y_pos)
            color_distance = max(
                abs(pixel_rgba[0] - background_rgba[0]),
                abs(pixel_rgba[1] - background_rgba[1]),
                abs(pixel_rgba[2] - background_rgba[2]),
            )
            alpha_distance = abs(pixel_rgba[3] - background_rgba[3])
            if color_distance <= color_threshold and alpha_distance <= alpha_threshold:
                continue
            found_pixels += 1
            min_x = min(min_x, x_pos)
            min_y = min(min_y, y_pos)
            max_x = max(max_x, x_pos)
            max_y = max(max_y, y_pos)

    if found_pixels <= 0 or max_x < min_x or max_y < min_y:
        return None

    bbox_width = (max_x - min_x) + 1
    bbox_height = (max_y - min_y) + 1
    return {
        "image_width": width,
        "image_height": height,
        "found_pixels": found_pixels,
        "required_pixels": required_pixels,
        "bbox_width": bbox_width,
        "bbox_height": bbox_height,
        "bbox_area_ratio": float(bbox_width * bbox_height) / float(width * height),
    }


def _saved_preview_has_content(image_path):
    metrics = _saved_preview_metrics(image_path)
    if not metrics:
        return False

    if metrics["found_pixels"] < metrics["required_pixels"]:
        return False

    min_bbox_width = metrics["image_width"] * 0.08
    min_bbox_height = metrics["image_height"] * 0.08
    if metrics["bbox_width"] < min_bbox_width and metrics["bbox_height"] < min_bbox_height:
        return False

    return True


def _apply_active_view_capture_padding(view, steps=2):
    if view is None:
        return

    step_count = max(0, int(steps))
    for _index in range(step_count):
        if hasattr(view, "zoomOut"):
            try:
                view.zoomOut()
                continue
            except Exception:
                pass
        if Gui is not None and hasattr(Gui, "SendMsgToActiveView"):
            try:
                Gui.SendMsgToActiveView("ViewZoomOut")
            except Exception:
                break


def _set_active_view_perspective(view):
    if view is None:
        return

    if hasattr(view, "setCameraType"):
        for camera_type in ("Perspective", "PerspectiveCamera"):
            try:
                view.setCameraType(camera_type)
                return
            except Exception:
                pass
    if hasattr(view, "viewPerspective"):
        try:
            view.viewPerspective()
            return
        except Exception:
            pass
    if Gui is not None and hasattr(Gui, "SendMsgToActiveView"):
        try:
            Gui.SendMsgToActiveView("ViewPerspective")
        except Exception:
            pass


def _capture_object_visual_state(obj):
    view_object = getattr(obj, "ViewObject", None)
    if view_object is None:
        return None

    shape = getattr(obj, "Shape", None)
    face_count = len(list(getattr(shape, "Faces", []) or [])) if shape is not None and not shape.isNull() else 0
    shape_color = _current_view_shape_color(view_object)
    state = {
        "visible": bool(getattr(view_object, "Visibility", True)),
        "transparency": int(getattr(view_object, "Transparency", 0) or 0)
        if hasattr(view_object, "Transparency")
        else None,
        "display_mode": str(getattr(view_object, "DisplayMode", "") or "")
        if hasattr(view_object, "DisplayMode")
        else "",
        "line_color": _normalize_color_triplet(getattr(view_object, "LineColor", None))
        if hasattr(view_object, "LineColor")
        else None,
        "point_color": _normalize_color_triplet(getattr(view_object, "PointColor", None))
        if hasattr(view_object, "PointColor")
        else None,
        "line_width": float(getattr(view_object, "LineWidth", 0.0) or 0.0)
        if hasattr(view_object, "LineWidth")
        else None,
        "shape_color": shape_color,
        "diffuse_colors": _current_view_diffuse_colors(view_object, face_count, shape_color),
        "face_count": face_count,
    }
    return state


def _restore_object_visual_state(obj, state):
    if not state:
        return
    view_object = getattr(obj, "ViewObject", None)
    if view_object is None:
        return

    if "visible" in state and hasattr(view_object, "Visibility"):
        try:
            view_object.Visibility = bool(state["visible"])
        except Exception:
            pass
    if state.get("transparency") is not None and hasattr(view_object, "Transparency"):
        try:
            view_object.Transparency = int(max(0, min(100, state["transparency"])))
        except Exception:
            pass
    if "display_mode" in state and hasattr(view_object, "DisplayMode"):
        try:
            display_mode = str(state.get("display_mode", "") or "").strip()
            if display_mode:
                view_object.DisplayMode = display_mode
        except Exception:
            pass
    if "line_color" in state and state.get("line_color") is not None and hasattr(view_object, "LineColor"):
        try:
            view_object.LineColor = tuple(state["line_color"])
        except Exception:
            pass
    if "point_color" in state and state.get("point_color") is not None and hasattr(view_object, "PointColor"):
        try:
            view_object.PointColor = tuple(state["point_color"])
        except Exception:
            pass
    if "line_width" in state and state.get("line_width") is not None and hasattr(view_object, "LineWidth"):
        try:
            view_object.LineWidth = float(state["line_width"])
        except Exception:
            pass
    if "shape_color" in state and hasattr(view_object, "ShapeColor"):
        try:
            view_object.ShapeColor = tuple(state["shape_color"])
        except Exception:
            pass
    if "diffuse_colors" in state and hasattr(view_object, "DiffuseColor"):
        try:
            view_object.DiffuseColor = tuple(state["diffuse_colors"])
        except Exception:
            pass


def _restore_object_emphasis_state(obj, state):
    if not state:
        return
    view_object = getattr(obj, "ViewObject", None)
    if view_object is None:
        return

    if "visible" in state and hasattr(view_object, "Visibility"):
        try:
            view_object.Visibility = bool(state["visible"])
        except Exception:
            pass
    if "line_color" in state and state.get("line_color") is not None and hasattr(view_object, "LineColor"):
        try:
            view_object.LineColor = tuple(state["line_color"])
        except Exception:
            pass
    if "point_color" in state and state.get("point_color") is not None and hasattr(view_object, "PointColor"):
        try:
            view_object.PointColor = tuple(state["point_color"])
        except Exception:
            pass
    if "line_width" in state and state.get("line_width") is not None and hasattr(view_object, "LineWidth"):
        try:
            view_object.LineWidth = float(state["line_width"])
        except Exception:
            pass
    if "shape_color" in state and state.get("shape_color") is not None and hasattr(view_object, "ShapeColor"):
        try:
            view_object.ShapeColor = tuple(state["shape_color"])
        except Exception:
            pass
    if "diffuse_colors" in state and state.get("diffuse_colors") is not None and hasattr(view_object, "DiffuseColor"):
        try:
            view_object.DiffuseColor = tuple(state["diffuse_colors"])
        except Exception:
            pass


def _set_object_visibility(obj, visible):
    view_object = getattr(obj, "ViewObject", None)
    if view_object is None or not hasattr(view_object, "Visibility"):
        return
    try:
        view_object.Visibility = bool(visible)
    except Exception:
        pass


def _set_object_context_visual_state(obj, color, transparency=None):
    view_object = getattr(obj, "ViewObject", None)
    if view_object is None:
        return
    shape = getattr(obj, "Shape", None)
    face_count = len(list(getattr(shape, "Faces", []) or [])) if shape is not None and not shape.isNull() else 0
    normalized_color = _normalize_color_triplet(color)

    if hasattr(view_object, "Visibility"):
        try:
            view_object.Visibility = True
        except Exception:
            pass
    if transparency is not None and hasattr(view_object, "Transparency"):
        try:
            view_object.Transparency = int(max(0, min(100, round(transparency))))
        except Exception:
            pass
    if hasattr(view_object, "DisplayMode"):
        for display_mode in ("Flat Lines", "Shaded", "Wireframe"):
            try:
                view_object.DisplayMode = display_mode
                break
            except Exception:
                pass
    if hasattr(view_object, "LineColor"):
        try:
            view_object.LineColor = ASSEMBLY_GUIDE_CONTEXT_LINE_COLOR
        except Exception:
            pass
    if hasattr(view_object, "PointColor"):
        try:
            view_object.PointColor = ASSEMBLY_GUIDE_CONTEXT_LINE_COLOR
        except Exception:
            pass
    if hasattr(view_object, "LineWidth"):
        try:
            view_object.LineWidth = float(ASSEMBLY_GUIDE_CONTEXT_LINE_WIDTH)
        except Exception:
            pass
    if hasattr(view_object, "ShapeColor"):
        try:
            view_object.ShapeColor = normalized_color
        except Exception:
            pass
    if face_count > 0 and hasattr(view_object, "DiffuseColor"):
        try:
            view_object.DiffuseColor = tuple([normalized_color] * face_count)
        except Exception:
            pass


def _object_occurrence_full_face_index_map(obj):
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return shape, {}

    solid_contexts = _solid_contexts_for_shape(shape)
    if not solid_contexts:
        return shape, {1: list(range(len(list(getattr(shape, "Faces", []) or []))))}

    face_map = {}
    for face_index, face in enumerate(list(getattr(shape, "Faces", []) or [])):
        try:
            solid_context = _solid_context_for_face(face, solid_contexts)
            occurrence_index = int((solid_context or {}).get("solid_index", 0)) + 1
        except Exception:
            occurrence_index = 1
        face_map.setdefault(occurrence_index, []).append(face_index)
    return shape, face_map


def _set_object_target_visual_state(
    obj,
    occurrence_index,
    context_color,
    target_color,
    target_transparency=ASSEMBLY_GUIDE_TARGET_TRANSPARENCY,
):
    view_object = getattr(obj, "ViewObject", None)
    if view_object is None:
        return

    shape, occurrence_face_map = _object_occurrence_full_face_index_map(obj)
    faces = list(getattr(shape, "Faces", []) or [])
    face_count = len(faces)
    normalized_context = _normalize_color_triplet(context_color)
    normalized_target = _normalize_color_triplet(target_color)

    if hasattr(view_object, "Visibility"):
        try:
            view_object.Visibility = True
        except Exception:
            pass
    if target_transparency is not None and hasattr(view_object, "Transparency"):
        try:
            view_object.Transparency = int(max(0, min(100, round(target_transparency))))
        except Exception:
            pass
    if hasattr(view_object, "DisplayMode"):
        for display_mode in ("Flat Lines", "Shaded", "Wireframe"):
            try:
                view_object.DisplayMode = display_mode
                break
            except Exception:
                pass
    if hasattr(view_object, "LineColor"):
        try:
            view_object.LineColor = ASSEMBLY_GUIDE_TARGET_LINE_COLOR
        except Exception:
            pass
    if hasattr(view_object, "PointColor"):
        try:
            view_object.PointColor = ASSEMBLY_GUIDE_TARGET_LINE_COLOR
        except Exception:
            pass
    if hasattr(view_object, "LineWidth"):
        try:
            view_object.LineWidth = float(ASSEMBLY_GUIDE_TARGET_LINE_WIDTH)
        except Exception:
            pass

    if face_count <= 0:
        if hasattr(view_object, "ShapeColor"):
            try:
                view_object.ShapeColor = normalized_target
            except Exception:
                pass
        return

    target_indices = occurrence_face_map.get(max(1, int(occurrence_index or 1)), [])
    if not target_indices and occurrence_face_map:
        target_indices = next(iter(occurrence_face_map.values()))
    if not target_indices:
        target_indices = list(range(face_count))

    diffuse_colors = [normalized_context] * face_count
    for face_index in target_indices:
        if 0 <= face_index < face_count:
            diffuse_colors[face_index] = normalized_target

    if hasattr(view_object, "ShapeColor"):
        try:
            view_object.ShapeColor = normalized_context
        except Exception:
            pass
    if hasattr(view_object, "DiffuseColor"):
        try:
            view_object.DiffuseColor = tuple(diffuse_colors)
        except Exception:
            pass


def _set_object_target_visual_state_preserving_current_visual(
    obj,
    occurrence_index,
    preserved_state,
    target_color,
):
    view_object = getattr(obj, "ViewObject", None)
    if view_object is None:
        return

    _restore_object_emphasis_state(obj, preserved_state or {})
    _set_object_visibility(obj, True)

    normalized_target = _normalize_color_triplet(target_color)
    shape, occurrence_face_map = _object_occurrence_full_face_index_map(obj)
    face_count = len(list(getattr(shape, "Faces", []) or []))
    target_indices = occurrence_face_map.get(max(1, int(occurrence_index or 1)), [])
    if not target_indices and occurrence_face_map:
        target_indices = next(iter(occurrence_face_map.values()))

    if hasattr(view_object, "ShapeColor"):
        try:
            view_object.ShapeColor = normalized_target
        except Exception:
            pass

    if face_count > 0 and hasattr(view_object, "DiffuseColor"):
        try:
            base_diffuse = list((preserved_state or {}).get("diffuse_colors") or [])
            if len(base_diffuse) != face_count:
                fallback_color = (preserved_state or {}).get("shape_color") or normalized_target
                base_diffuse = [fallback_color] * face_count
            if not target_indices:
                target_indices = list(range(face_count))
            for face_index in target_indices:
                if 0 <= face_index < face_count:
                    base_diffuse[face_index] = normalized_target
            view_object.DiffuseColor = tuple(base_diffuse)
        except Exception:
            pass

    return


def _copy_toposhape(shape):
    if shape is None:
        return None
    for method_name in ("copy", "copyShape"):
        copier = getattr(shape, method_name, None)
        if not callable(copier):
            continue
        try:
            copied_shape = copier()
        except Exception:
            continue
        if copied_shape is None:
            continue
        try:
            if copied_shape.isNull():
                continue
        except Exception:
            pass
        return copied_shape
    return shape


def _assembly_overlay_shape_for_object(obj, occurrence_index):
    shape = getattr(obj, "Shape", None)
    if shape is None:
        return None
    try:
        if shape.isNull():
            return None
    except Exception:
        return None

    solids = _shape_solid_list(shape)
    overlay_shape = shape
    solid_index = max(1, int(occurrence_index or 1)) - 1
    if len(solids) > 1 and 0 <= solid_index < len(solids):
        overlay_shape = solids[solid_index]

    return _copy_toposhape(overlay_shape)


def _ensure_assembly_overlay_object(document):
    if document is None or Part is None:
        return None

    overlay = document.getObject("PanelNestAssemblyGuideOverlay")
    if overlay is None:
        try:
            overlay = document.addObject("Part::Feature", "PanelNestAssemblyGuideOverlay")
        except Exception:
            return None
        try:
            overlay.Label = "PanelNest Assembly Guide Overlay"
        except Exception:
            pass
        try:
            if not hasattr(overlay, INTERNAL_PROPERTY_NAME):
                overlay.addProperty("App::PropertyString", INTERNAL_PROPERTY_NAME, PART_PROPERTY_GROUP)
            setattr(overlay, INTERNAL_PROPERTY_NAME, "assembly_guide_overlay")
        except Exception:
            pass

    view_object = getattr(overlay, "ViewObject", None)
    if view_object is not None:
        if hasattr(view_object, "ShapeColor"):
            try:
                view_object.ShapeColor = _normalize_color_triplet(ASSEMBLY_GUIDE_TARGET_COLOR)
            except Exception:
                pass
        if hasattr(view_object, "LineColor"):
            try:
                view_object.LineColor = _normalize_color_triplet(ASSEMBLY_GUIDE_TARGET_LINE_COLOR)
            except Exception:
                pass
        if hasattr(view_object, "PointColor"):
            try:
                view_object.PointColor = _normalize_color_triplet(ASSEMBLY_GUIDE_TARGET_LINE_COLOR)
            except Exception:
                pass
        if hasattr(view_object, "LineWidth"):
            try:
                view_object.LineWidth = float(ASSEMBLY_GUIDE_TARGET_LINE_WIDTH)
            except Exception:
                pass
        if hasattr(view_object, "Transparency"):
            try:
                view_object.Transparency = int(ASSEMBLY_GUIDE_MANUAL_TARGET_TRANSPARENCY)
            except Exception:
                pass
        if hasattr(view_object, "DisplayMode"):
            try:
                display_mode = str(getattr(view_object, "DisplayMode", "") or "").strip()
                if not display_mode:
                    view_object.DisplayMode = "Flat Lines"
            except Exception:
                pass
        if hasattr(view_object, "Selectable"):
            try:
                view_object.Selectable = False
            except Exception:
                pass
        if hasattr(view_object, "ShowInTree"):
            try:
                view_object.ShowInTree = False
            except Exception:
                pass
    _set_object_visibility(overlay, False)
    return overlay


def _set_assembly_overlay_for_record(overlay_object, target_object, occurrence_index):
    if overlay_object is None:
        return False

    if target_object is None:
        _set_object_visibility(overlay_object, False)
        return False

    overlay_shape = _assembly_overlay_shape_for_object(target_object, occurrence_index)
    if overlay_shape is None:
        _set_object_visibility(overlay_object, False)
        return False

    try:
        overlay_object.Shape = overlay_shape
    except Exception:
        _set_object_visibility(overlay_object, False)
        return False
    _set_object_visibility(overlay_object, True)
    return True


def _is_layout_internal_object(obj):
    if obj is None or not _is_internal_object(obj):
        return False
    internal_type = str(getattr(obj, INTERNAL_PROPERTY_NAME, "") or "")
    return internal_type.startswith("layout_")


def _document_has_manual_transparency(document=None):
    doc = document or ensure_document()
    for obj in list(getattr(doc, "Objects", []) or []):
        view_object = getattr(obj, "ViewObject", None)
        if view_object is None or not hasattr(view_object, "Transparency"):
            continue
        try:
            transparency = int(getattr(view_object, "Transparency", 0) or 0)
        except Exception:
            transparency = 0
        if transparency > 0:
            return True
    return False


def _active_view_image_data_uri(
    view,
    image_path,
    width_px=ASSEMBLY_GUIDE_IMAGE_WIDTH_PX,
    height_px=ASSEMBLY_GUIDE_IMAGE_HEIGHT_PX,
    allow_refit=True,
    use_viewport_grab=False,
):
    if view is None:
        return ""

    if use_viewport_grab:
        try:
            if os.path.exists(image_path):
                os.remove(image_path)
        except OSError:
            pass
        _safe_gui_refresh()
        time.sleep(0.08)
        if _save_active_view_widget_snapshot(image_path, width_px=width_px, height_px=height_px):
            if os.path.exists(image_path) and os.path.getsize(image_path) > 0:
                try:
                    with open(image_path, "rb") as handle:
                        encoded = base64.b64encode(handle.read()).decode("ascii")
                    return f"data:image/png;base64,{encoded}"
                except Exception:
                    return ""
        return ""

    save_attempts = (
        {"arguments": (image_path, width_px, height_px, "Current"), "refit": False},
        {"arguments": (image_path, width_px, height_px, "Current"), "refit": True},
        {"arguments": (image_path, width_px, height_px), "refit": True},
    )
    for attempt_index, arguments in enumerate(save_attempts, start=1):
        try:
            if os.path.exists(image_path):
                os.remove(image_path)
        except OSError:
            pass
        try:
            if attempt_index > 1:
                if allow_refit and arguments.get("refit"):
                    if hasattr(view, "fitAll"):
                        try:
                            view.fitAll()
                        except Exception:
                            pass
                    _apply_active_view_capture_padding(view, steps=1)
                _safe_gui_refresh()
                time.sleep(0.12)
            view.saveImage(*arguments["arguments"])
        except Exception:
            continue
        if os.path.exists(image_path) and os.path.getsize(image_path) > 0:
            try:
                if not _saved_preview_has_content(image_path):
                    continue
                with open(image_path, "rb") as handle:
                    encoded = base64.b64encode(handle.read()).decode("ascii")
                return f"data:image/png;base64,{encoded}"
            except Exception:
                return ""
    return ""


def capture_panelnest_assembly_preview_map(document=None, records=None, parts=None):
    if Gui is None:
        return {}

    doc = document or ensure_document()
    records = records if records is not None else _lazy_collect_part_label_records(parts=parts, document=doc)
    if not records:
        return {}

    active_view = _active_view_for_document(doc)
    if active_view is None:
        return {}

    source_objects = []
    source_by_name = {}
    seen_source_names = set()
    for record in records:
        base_object_name, _occurrence_index = _parse_occurrence_object_name(record.get("object_name"))
        if not base_object_name or base_object_name in seen_source_names:
            continue
        obj = doc.getObject(base_object_name)
        if obj is None or not _is_candidate_object(obj):
            continue
        seen_source_names.add(base_object_name)
        source_objects.append(obj)
        source_by_name[base_object_name] = obj

    if not source_objects:
        return {}

    try:
        settings = _lazy_get_sheet_settings()
    except Exception:
        settings = SheetSettings()
    preserve_current_visual = bool(
        getattr(settings, "assembly_guide_preserve_current_visual", False)
    )
    if not preserve_current_visual and _document_has_manual_transparency(doc):
        preserve_current_visual = True

    managed_objects = []
    original_states = {}
    visible_container_names = set()
    layout_root = doc.getObject(LAYOUT_ROOT_NAME)
    if not preserve_current_visual:
        for obj in list(getattr(doc, "Objects", []) or []):
            if getattr(obj, "ViewObject", None) is None:
                continue
            if getattr(obj, "Name", "") == LAYOUT_ROOT_NAME:
                managed_objects.append(obj)
                continue
            if _lazy_is_user_project_container(obj) or _is_candidate_object(obj):
                managed_objects.append(obj)
        original_states = {obj.Name: _capture_object_visual_state(obj) for obj in managed_objects}
        for obj in source_objects:
            for _depth, container in _lazy_ancestor_user_project_containers(obj):
                visible_container_names.add(container.Name)
        if layout_root is not None:
            visible_container_names.discard(layout_root.Name)

    original_camera = ""
    if hasattr(active_view, "getCamera"):
        try:
            original_camera = active_view.getCamera()
        except Exception:
            original_camera = ""
    original_selection_state = _capture_gui_selection_state(document=doc)
    preserve_layout_states = {}
    preserve_layout_objects = []
    preserve_overlay_object = None

    temp_dir = tempfile.mkdtemp(prefix="panelnest_assembly_guide_")
    preview_map = {}

    try:
        _clear_gui_selection()

        if preserve_current_visual:
            preserve_layout_objects = [
                obj
                for obj in list(getattr(doc, "Objects", []) or [])
                if getattr(obj, "ViewObject", None) is not None and _is_layout_internal_object(obj)
            ]
            preserve_layout_states = {
                obj.Name: _capture_object_visual_state(obj) for obj in preserve_layout_objects
            }
            for layout_obj in preserve_layout_objects:
                _set_object_visibility(layout_obj, False)
            preserve_overlay_object = _ensure_assembly_overlay_object(doc)
        else:
            for obj in managed_objects:
                _set_object_visibility(obj, False)

            for container_name in visible_container_names:
                container = doc.getObject(container_name)
                if container is not None:
                    _set_object_visibility(container, True)

            for obj in source_objects:
                _set_object_context_visual_state(
                    obj,
                    ASSEMBLY_GUIDE_CONTEXT_COLOR,
                    None,
                )

            gui_toggle_applied = _toggle_gui_transparency_for_objects(source_objects, document=doc)
            if not gui_toggle_applied:
                for obj in source_objects:
                    _set_object_context_visual_state(
                        obj,
                        ASSEMBLY_GUIDE_CONTEXT_COLOR,
                        ASSEMBLY_GUIDE_CONTEXT_TRANSPARENCY,
                    )
            else:
                for obj in source_objects:
                    view_object = getattr(obj, "ViewObject", None)
                    if view_object is None:
                        continue
                    if hasattr(view_object, "LineColor"):
                        try:
                            view_object.LineColor = ASSEMBLY_GUIDE_CONTEXT_LINE_COLOR
                        except Exception:
                            pass
                    if hasattr(view_object, "PointColor"):
                        try:
                            view_object.PointColor = ASSEMBLY_GUIDE_CONTEXT_LINE_COLOR
                        except Exception:
                            pass
                    if hasattr(view_object, "LineWidth"):
                        try:
                            view_object.LineWidth = float(ASSEMBLY_GUIDE_CONTEXT_LINE_WIDTH)
                        except Exception:
                            pass

            context_states = {obj.Name: _capture_object_visual_state(obj) for obj in source_objects}

        if not preserve_current_visual and layout_root is not None:
            _set_object_visibility(layout_root, False)

        if not preserve_current_visual:
            _set_active_view_perspective(active_view)

            if hasattr(active_view, "viewIsometric"):
                try:
                    active_view.viewIsometric()
                except Exception:
                    pass
            elif Gui is not None and hasattr(Gui, "SendMsgToActiveView"):
                try:
                    Gui.SendMsgToActiveView("ViewIsometric")
                except Exception:
                    pass

            if hasattr(active_view, "fitAll"):
                try:
                    active_view.fitAll()
                except Exception:
                    pass
            elif Gui is not None and hasattr(Gui, "SendMsgToActiveView"):
                try:
                    Gui.SendMsgToActiveView("ViewFit")
                except Exception:
                    pass
            _apply_active_view_capture_padding(active_view, steps=1)
        _safe_gui_refresh()
        time.sleep(0.06)

        for record_index, record in enumerate(records, start=1):
            base_object_name, occurrence_index = _parse_occurrence_object_name(record.get("object_name"))
            target_object = source_by_name.get(base_object_name)
            if preserve_current_visual:
                _set_assembly_overlay_for_record(
                    preserve_overlay_object,
                    target_object,
                    occurrence_index,
                )
            else:
                for obj in source_objects:
                    _restore_object_visual_state(obj, context_states.get(obj.Name))
                if target_object is not None:
                    _set_object_target_visual_state(
                        target_object,
                        occurrence_index,
                        ASSEMBLY_GUIDE_CONTEXT_COLOR,
                        ASSEMBLY_GUIDE_TARGET_COLOR,
                        ASSEMBLY_GUIDE_TARGET_TRANSPARENCY,
                    )

            _safe_gui_refresh()
            time.sleep(0.03)

            image_path = os.path.join(temp_dir, "piece_%03d.png" % record_index)
            preview_data_uri = _active_view_image_data_uri(
                active_view,
                image_path,
                allow_refit=not preserve_current_visual,
                use_viewport_grab=preserve_current_visual,
            )
            if preview_data_uri:
                preview_map[_assembly_record_key(record)] = preview_data_uri
    finally:
        if preserve_current_visual:
            if preserve_overlay_object is not None:
                overlay_name = getattr(preserve_overlay_object, "Name", "")
                try:
                    _set_object_visibility(preserve_overlay_object, False)
                except Exception:
                    pass
                try:
                    if overlay_name:
                        doc.removeObject(overlay_name)
                except Exception:
                    pass
            for layout_obj in preserve_layout_objects:
                _restore_object_visual_state(layout_obj, preserve_layout_states.get(layout_obj.Name))
        else:
            for obj in managed_objects:
                _restore_object_visual_state(obj, original_states.get(obj.Name))
        if not preserve_current_visual and original_camera and hasattr(active_view, "setCamera"):
            try:
                active_view.setCamera(original_camera)
            except Exception:
                pass
        _restore_gui_selection_state(original_selection_state, document=doc)
        _safe_gui_refresh()
        shutil.rmtree(temp_dir, ignore_errors=True)

    return preview_map
