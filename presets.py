import math


DEFAULT_PRESETS = {
    "material_thickness": 15.0,
    "depth_extra": 0.5,
    "tool_diameter": 6.0,
    "stepdown": 3.0,
    "feed_xy": 1800.0,
    "feed_z": 500.0,
    "rapid_feed": 4000.0,
    "safe_height": 8.0,
    "retract_height": 15.0,
    "ramp_length": 30.0,
    "rpm": 18000,
    "output_path": "woodcam2d_output.nc",
    "cut_side": "outside",
    "job_width": 0.0,
    "job_height": 0.0,
    "job_depth": 0.0,
    "job_type": "single_sided",
    "job_z_zero_mode": "material_surface",
    "job_origin_anchor": "bottom_left",
    "job_use_selection_bounds_origin": False,
    "job_origin_x": 0.0,
    "job_origin_y": 0.0,
    "z_zero_mode": "material_surface",
    "origin_anchor": "bottom_left",
    "use_selection_bounds_origin": False,
    "origin_x": 0.0,
    "origin_y": 0.0,
    "model_position_mode": "gap_above",
    "model_gap_above": 0.0,
    "model_gap_below": 0.0,
    "machine_x_size": 0.0,
    "machine_y_size": 0.0,
    "start_x": 0.0,
    "start_y": 0.0,
    "return_to_start": True,
    "operation_start_depth": 0.0,
    "operation_cut_depth": 15.5,
    "cut_allowance_offset": 0.0,
    "common_line_tolerance": 0.02,
    "cut_depth_strategy": "hybrid_piece_bidirectional",
    "tab_release_mode": "keep_tabs",
    "tab_release_supervision": "per_piece",
    "tab_release_ramp_angle_degrees": 12.0,
    "tab_release_minimum_depth_step": 0.5,
    "spindle_spinup_seconds": 1.0,
    "loose_waste_fixation": "disabled",
    "screw_pilot_diameter": 0.0,
    "screw_pilot_depth": 0.0,
    "screw_head_diameter": 10.0,
    "screw_safety_margin": 3.0,
    "screw_head_height": 3.0,
    "cut_last_pass_allowance": 0.0,
    "tab_length": 12.0,
    "tab_thickness": 3.0,
    "tab_surface_clearance": 0.2,
    "tab_count": 4,
    "tab_best_fixation": True,
    "use_helical_drilling": True,
    "helix_pitch": 1.0,
    "helix_stepover_percent": 40.0,
    "hole_counterbore_diameter": 10.0,
    "hole_counterbore_depth": 3.0,
    "peck_step": 3.0,
    "peck_retract_clearance": 0.5,
    "dwell_seconds": 0.0,
    "pocket_cut_depth": 2.0,
    "pocket_stepover_percent": 40.0,
    "pocket_allowance": 0.0,
    "pocket_raster_angle": 0.0,
    "use_ramp": True,
    "corner_slowdown_enabled": True,
    "corner_angle_threshold": 45.0,
    "corner_feed_percent": 40.0,
    "corner_slowdown_distance": 8.0,
    "simulation_speed_multiplier": 1.0,
    "rough3d_allowance": 0.5,
    "rough3d_boundary_offset": 0.0,
    "rough3d_stepover_percent": 40.0,
    "finish3d_boundary_offset": 0.0,
    "finish3d_stepover_percent": 10.0,
    "finish3d_raster_angle": 0.0,
    "surface_sampling": 0.5,
}

MATERIAL_PRESETS = {
    "MDF 15 mm": {
        "material_thickness": 15.0,
        "depth_extra": 0.5,
        "safe_height": 8.0,
        "retract_height": 15.0,
    },
    "MDF 18 mm": {
        "material_thickness": 18.0,
        "depth_extra": 0.5,
        "safe_height": 8.0,
        "retract_height": 15.0,
    },
}

TOOL_PRESETS = {
    "Fresa 6 mm MDF": {
        "tool_type": "end_mill",
        "notes": "Fresa de topo reto para corte 2D, perfil e rebaixo com fundo plano.",
        "tool_diameter": 6.0,
        "stepdown": 3.0,
        "stepover": 2.1,
        "stepover_percent": 35.0,
        "included_angle": 0.0,
        "feed_xy": 1800.0,
        "feed_z": 500.0,
        "rapid_feed": 4000.0,
        "ramp_length": 30.0,
        "rpm": 18000,
        "tool_number": 1,
    },
    "Fresa 3 mm MDF": {
        "tool_type": "end_mill",
        "notes": "Fresa de topo menor para detalhes, rasgos estreitos e peças pequenas.",
        "tool_diameter": 3.0,
        "stepdown": 1.5,
        "stepover": 1.05,
        "stepover_percent": 35.0,
        "included_angle": 0.0,
        "feed_xy": 1200.0,
        "feed_z": 350.0,
        "rapid_feed": 4000.0,
        "ramp_length": 25.0,
        "rpm": 20000,
        "tool_number": 2,
    },
    "Broca 6 mm": {
        "tool_type": "drill",
        "notes": "Broca para furação vertical. Use em furos; não é ideal para cortar lateralmente.",
        "tool_diameter": 6.0,
        "stepdown": 3.0,
        "stepover": 0.0,
        "stepover_percent": 0.0,
        "included_angle": 118.0,
        "feed_xy": 800.0,
        "feed_z": 500.0,
        "rapid_feed": 4000.0,
        "ramp_length": 0.0,
        "rpm": 14000,
        "tool_number": 3,
    },
    "V-Bit 90° 6 mm": {
        "tool_type": "v_bit",
        "notes": "Fresa em V para gravação, chanfro e futuros percursos V-carve.",
        "tool_diameter": 6.0,
        "stepdown": 2.0,
        "stepover": 1.5,
        "stepover_percent": 25.0,
        "included_angle": 90.0,
        "feed_xy": 1200.0,
        "feed_z": 300.0,
        "rapid_feed": 4000.0,
        "ramp_length": 10.0,
        "rpm": 18000,
        "tool_number": 4,
    },
    "Topo esférico 6 mm": {
        "tool_type": "ball_nose",
        "notes": "Fresa esférica para acabamento 3D e relevos; deixa fundo arredondado.",
        "tool_diameter": 6.0,
        "stepdown": 2.0,
        "stepover": 0.6,
        "stepover_percent": 10.0,
        "included_angle": 0.0,
        "feed_xy": 1600.0,
        "feed_z": 400.0,
        "rapid_feed": 4000.0,
        "ramp_length": 20.0,
        "rpm": 18000,
        "tool_number": 5,
    },
    "Fresa compressão 6 mm": {
        "tool_type": "compression",
        "notes": "Fresa de compressão para chapas laminadas; reduz lascas na face superior e inferior.",
        "tool_diameter": 6.0,
        "stepdown": 4.0,
        "stepover": 2.1,
        "stepover_percent": 35.0,
        "included_angle": 0.0,
        "feed_xy": 2200.0,
        "feed_z": 500.0,
        "rapid_feed": 4000.0,
        "ramp_length": 30.0,
        "rpm": 18000,
        "tool_number": 6,
    },
    "Faceadora 20 mm": {
        "tool_type": "surfacing",
        "notes": "Fresa larga para plainar mesa/spoilboard ou regularizar superfície.",
        "tool_diameter": 20.0,
        "stepdown": 0.8,
        "stepover": 8.0,
        "stepover_percent": 40.0,
        "included_angle": 0.0,
        "feed_xy": 2500.0,
        "feed_z": 300.0,
        "rapid_feed": 4000.0,
        "ramp_length": 20.0,
        "rpm": 14000,
        "tool_number": 7,
    },
}

MACHINE_LIMITS = {
    "max_plunge": 1000.0,
    "max_feed_xy": 4000.0,
    # Zero mantém a validação dimensional desativada até o usuário informar
    # o curso real da própria máquina.
    "x_size": 0.0,
    "y_size": 0.0,
    "z_min": -80.0,
    "z_max": 80.0,
}


def normalize_work_area_presets(values):
    """Return safe, ordered work-area presets from persisted JSON data.

    Presets are personal convenience data, not project geometry.  The helper
    deliberately accepts both the current list format and an older/name-keyed
    dictionary so a malformed preference can never prevent WoodCAM opening.
    """

    if isinstance(values, dict):
        source = []
        for name, item in values.items():
            if not isinstance(item, dict):
                continue
            normalized_item = dict(item)
            normalized_item["name"] = name
            source.append(normalized_item)
    elif isinstance(values, (tuple, list)):
        source = values
    else:
        return []
    result = []
    seen = set()
    for raw in source:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name", "")).strip()
        key = name.casefold()
        try:
            width = float(raw.get("width", 0.0))
            height = float(raw.get("height", 0.0))
            depth = float(raw.get("depth", 0.0))
        except (TypeError, ValueError):
            continue
        if (
            not name
            or key in seen
            or not all(math.isfinite(value) for value in (width, height, depth))
            or width <= 0.0
            or height <= 0.0
            or depth < 0.0
        ):
            continue
        seen.add(key)
        result.append(
            {
                "name": name,
                "width": width,
                "height": height,
                "depth": depth,
            }
        )
    return result
