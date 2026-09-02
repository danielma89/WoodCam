"""Validação de integração do nesting por formas dentro do FreeCAD.

Uso:
    PANELNEST_TEST_PROJECT=/caminho/projeto.FCStd FreeCAD --console \
        scripts/check-shape-layout-project.py
"""

import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import FreeCAD as App


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import panelnest
from panelnest.constants import INTERNAL_PROPERTY_NAME
from panelnest.nesting import _effective_margin_mm, _effective_spacing_mm


def _layout_extent(layout_sheet, margin_mm):
    return panelnest.layout_shape_score(layout_sheet.placements, margin_mm)


def main():
    project_path = os.environ.get("PANELNEST_TEST_PROJECT", "").strip()
    if not project_path:
        raise RuntimeError("Defina PANELNEST_TEST_PROJECT com o arquivo FCStd a validar.")

    document = App.openDocument(str(Path(project_path).expanduser().resolve()))
    try:
        parts = panelnest.collect_parts(include_hidden=True)
        settings = panelnest.get_sheet_settings()
        if os.environ.get("PANELNEST_ONLY_PARTS"):
            groups = panelnest.group_parts(parts)
            print(
                "PANELNEST_PARTS_CHECK="
                + json.dumps(
                    {
                        "parts": len(parts),
                        "occurrences": sum(max(1, int(part.quantity or 1)) for part in parts),
                        "groups": [
                            {
                                "group_id": group["group_id"],
                                "material": group["material"],
                                "thickness_mm": group["thickness_mm"],
                                "cut_method": group["cut_method"],
                                "parts": [
                                    {
                                        "part": part.part_id,
                                        "label": part.label,
                                        "size": [part.length_mm, part.width_mm],
                                        "quantity": part.quantity,
                                        "allow_rotation": part.allow_rotation,
                                        "grain": part.grain_direction,
                                        "profile": bool(part.profile_points),
                                    }
                                    for part in group["parts"]
                                ],
                            }
                            for group in groups
                        ],
                    },
                    ensure_ascii=False,
                )
            )
            return
        fast_settings = replace(settings, cnc_nesting_mode="Rápido (retangular)")
        shape_settings = replace(settings, cnc_nesting_mode="Otimizado por formas")

        fast_sheets = panelnest.create_layout_sheets(parts, settings=fast_settings)
        shape_sheets = panelnest.create_layout_sheets(parts, settings=shape_settings)

        fast_piece_count = sum(len(sheet.placements) for sheet in fast_sheets)
        shape_piece_count = sum(len(sheet.placements) for sheet in shape_sheets)
        if fast_piece_count != shape_piece_count:
            raise AssertionError(
                f"Quantidade de peças mudou: rápido={fast_piece_count}, formas={shape_piece_count}."
            )
        if len(shape_sheets) > len(fast_sheets):
            raise AssertionError("O modo por formas abriu mais chapas que o modo rápido.")

        validations = []
        for sheet in shape_sheets:
            margin_mm = _effective_margin_mm(shape_settings, sheet.cut_method)
            spacing_mm = _effective_spacing_mm(shape_settings, sheet.cut_method)
            valid, reason = panelnest.validate_shape_placements(
                sheet.placements,
                sheet.source_length_mm,
                sheet.source_width_mm,
                margin_mm,
                spacing_mm,
            )
            validations.append({"valid": valid, "reason": reason})
            if not valid:
                raise AssertionError(reason)

        comparison = []
        for index, (fast_sheet, shape_sheet) in enumerate(
            zip(fast_sheets, shape_sheets),
            start=1,
        ):
            margin_mm = _effective_margin_mm(shape_settings, shape_sheet.cut_method)
            comparison.append(
                {
                    "sheet": index,
                    "fast_extent": _layout_extent(fast_sheet, margin_mm),
                    "shape_extent": _layout_extent(shape_sheet, margin_mm),
                    "strategy": shape_sheet.layout_strategy,
                }
            )

        model_sheets = []
        model_parts = []
        solid_overlap_pairs = []
        cam_solid_count = 0
        cam_overlap_pairs = []
        if not os.environ.get("PANELNEST_SKIP_MODEL"):
            model_sheets, _ = panelnest.create_layout_model(parts, settings=shape_settings)
            model_parts = [
                obj
                for obj in document.Objects
                if str(getattr(obj, INTERNAL_PROPERTY_NAME, "") or "") == "layout_part"
            ]
            if len(model_parts) != shape_piece_count:
                raise AssertionError(
                    f"O modelo criou {len(model_parts)} sólidos para {shape_piece_count} peças."
                )
            for left_index, left_object in enumerate(model_parts):
                for right_object in model_parts[left_index + 1:]:
                    common = left_object.Shape.common(right_object.Shape)
                    if float(getattr(common, "Volume", 0.0) or 0.0) > 1e-4:
                        solid_overlap_pairs.append([left_object.Name, right_object.Name])
            if solid_overlap_pairs:
                raise AssertionError(
                    f"Há sólidos sobrepostos no modelo: {solid_overlap_pairs[:3]}."
                )
            cam_objects = [
                obj
                for obj in document.Objects
                if str(getattr(obj, INTERNAL_PROPERTY_NAME, "") or "")
                == "layout_cam_compound"
            ]
            cam_solids = [
                solid
                for cam_object in cam_objects
                for solid in list(getattr(cam_object.Shape, "Solids", []) or [])
            ]
            cam_solid_count = len(cam_solids)
            if cam_solid_count != shape_piece_count:
                raise AssertionError(
                    f"O CAM contém {cam_solid_count} sólidos para {shape_piece_count} peças."
                )
            for left_index, left_solid in enumerate(cam_solids):
                for right_index, right_solid in enumerate(cam_solids[left_index + 1:], left_index + 1):
                    common = left_solid.common(right_solid)
                    if float(getattr(common, "Volume", 0.0) or 0.0) > 1e-4:
                        cam_overlap_pairs.append([left_index, right_index])
            if cam_overlap_pairs:
                raise AssertionError(
                    f"Há sólidos sobrepostos no CAM: {cam_overlap_pairs[:3]}."
                )

        result = {
            "parts": len(parts),
            "occurrences": shape_piece_count,
            "fast_sheets": len(fast_sheets),
            "shape_sheets": len(shape_sheets),
            "comparison": comparison,
            "validations": validations,
            "model_sheets": len(model_sheets),
            "model_layout_parts": len(model_parts),
            "solid_overlap_pairs": solid_overlap_pairs,
            "cam_solid_count": cam_solid_count,
            "cam_overlap_pairs": cam_overlap_pairs,
            "shape_placements": [
                {
                    "sheet": sheet.sheet_index,
                    "part": placement.part.part_id,
                    "source": [
                        placement.part.length_mm,
                        placement.part.width_mm,
                    ],
                    "position": [placement.x_mm, placement.y_mm],
                    "placed": [
                        placement.placed_length_mm,
                        placement.placed_width_mm,
                    ],
                    "rotated": placement.rotated,
                    "rotation_deg": panelnest.placement_rotation_deg(placement),
                    "profile": list(placement.part.profile_points or []),
                }
                for sheet in shape_sheets
                for placement in sheet.placements
            ],
        }
        print("PANELNEST_SHAPE_CHECK=" + json.dumps(result, ensure_ascii=False))
    finally:
        App.closeDocument(document.Name)


# O FreeCADCmd executa scripts com um nome de módulo próprio, não ``__main__``.
main()
