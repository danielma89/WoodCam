
HEADER_TEMPLATE = [
    "(WoodCAM2D GRBL Output)",
    "G21",  # milímetros
    "G90",  # coordenadas absolutas
    "G17",  # plano XY
    "G94",  # avanço por minuto
    "G54",  # sistema de trabalho padrão
]


def format_coordinate(value, precision=4):
    return f"{value:.{precision}f}" if value is not None else None


def build_line(command, x=None, y=None, z=None, f=None):
    parts = [command]
    if x is not None:
        parts.append(f"X{format_coordinate(x)}")
    if y is not None:
        parts.append(f"Y{format_coordinate(y)}")
    if z is not None:
        parts.append(f"Z{format_coordinate(z)}")
    if f is not None:
        parts.append(f"F{format_coordinate(f, precision=0)}")
    return " ".join(parts)


def build_gcode(
    rpm,
    feed_z,
    feed_xy,
    rapid_feed,
    safe_height,
    retract_height,
    moves,
    ramp_feed=None,
    job_name=None,
    home_x=0.0,
    home_y=0.0,
    z_zero_mode=None,
    material_thickness=None,
    tool_name=None,
    tool_number=None,
):
    if ramp_feed is None:
        ramp_feed = min(feed_xy * 0.6, feed_xy)

    lines = list(HEADER_TEMPLATE)
    if job_name:
        lines.append(f"(Etapa: {job_name})")
    if z_zero_mode:
        label = (
            "mesa da maquina"
            if z_zero_mode == "machine_bed"
            else "superficie do material"
        )
        lines.append(f"(Z zero: {label})")
    if material_thickness is not None:
        lines.append(
            f"(Espessura do material: {format_coordinate(float(material_thickness))} mm)"
        )
    if tool_name or tool_number:
        number_text = f"T{int(tool_number)}" if tool_number else "T?"
        lines.append(f"(Ferramenta {number_text}: {tool_name or 'não nomeada'})")
    lines.append(f"(Avanco rapido configurado na maquina: {format_coordinate(rapid_feed, precision=0)} mm/min)")
    lines.append(f"(Altura segura: {format_coordinate(safe_height)} mm)")
    lines.append(f"M3 S{int(rpm)}")
    lines.append(f"G0 Z{format_coordinate(retract_height)}")
    lines.append("")

    for move in moves:
        if move["type"] == "operator_pause":
            message = str(move.get("message", "Pausa do operador")).replace(
                ")", "]"
            )
            lines.append("M5")
            lines.append("(%s)" % message)
            lines.append("M0")
            if move.get("resume_spindle", True):
                lines.append(f"M3 S{int(rpm)}")
                spinup = max(0.0, float(move.get("spinup_seconds", 0.0) or 0.0))
                if spinup > 0.0:
                    lines.append(f"G4 P{format_coordinate(spinup, precision=3)}")
        elif move["type"] == "dwell":
            seconds = max(0.0, float(move.get("seconds", 0.0)))
            lines.append(f"G4 P{format_coordinate(seconds, precision=3)}")
        elif move["type"] == "rapid":
            if move["x"] is not None and move["y"] is not None:
                lines.append(build_line("G0", x=move["x"], y=move["y"], z=move["z"]))
            else:
                lines.append(build_line("G0", z=move["z"]))
        elif move["type"] in ("feed_plunge", "feed_drill"):
            lines.append(build_line("G1", x=move["x"], y=move["y"], z=move["z"], f=feed_z))
        elif move["type"] in ("feed_ramp", "feed_helix"):
            lines.append(build_line("G1", x=move["x"], y=move["y"], z=move["z"], f=ramp_feed))
        elif move["type"] == "feed_cut":
            move_feed = float(feed_xy) * float(move.get("feed_scale", 1.0))
            lines.append(
                build_line(
                    "G1",
                    x=move["x"],
                    y=move["y"],
                    z=move["z"],
                    f=move_feed,
                )
            )

    lines.append("M5")
    lines.append(f"G0 Z{format_coordinate(retract_height)}")
    lines.append(build_line("G0", x=float(home_x), y=float(home_y)))
    lines.append("M30")
    return lines


def save_gcode_file(path, lines):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return path
