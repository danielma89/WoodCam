"""Pure, explainable CAM setup advisor.

The advisor never edits settings and never authorizes G-code.  Hard safety
rules remain in :mod:`validators`; this module highlights conservative setup,
tool and finish observations before the normal preview/apply workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple


_LEVEL_ORDER = {"warning": 0, "recommendation": 1, "info": 2}


@dataclass(frozen=True)
class CAMAdvice:
    code: str
    level: str
    title: str
    explanation: str
    setting_key: str = ""
    current_value: Optional[float] = None
    suggested_value: Optional[float] = None


@dataclass(frozen=True)
class CAMAdviceReport:
    items: Tuple[CAMAdvice, ...]

    @property
    def warning_count(self) -> int:
        return sum(item.level == "warning" for item in self.items)

    @property
    def recommendation_count(self) -> int:
        return sum(item.level == "recommendation" for item in self.items)

    @property
    def is_clear(self) -> bool:
        return not self.items

    def to_plain_text(
        self,
        translator: Optional[Callable[[str], str]] = None,
    ) -> str:
        translate = translator or (lambda value: value)
        if not self.items:
            return translate(
                "Nenhum alerta conservador foi encontrado. Isso não substitui "
                "a pré-visualização, a simulação nem a conferência na máquina."
            )
        labels = {
            "warning": "ATENÇÃO",
            "recommendation": "SUGESTÃO",
            "info": "INFORMAÇÃO",
        }
        lines = []
        for item in self.items:
            lines.append(
                "[%s] %s"
                % (translate(labels[item.level]), translate(item.title))
            )
            lines.append(translate(item.explanation))
            if item.suggested_value is not None:
                lines.append(
                    translate("Valor sugerido para revisar: %g")
                    % item.suggested_value
                )
            lines.append("")
        lines.append(translate(
            "O assistente apenas analisa: nenhum parâmetro, geometria ou G-code foi alterado."
        ))
        return "\n".join(lines).strip()


def _number(settings: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(settings.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _finish_stepover(settings: Mapping[str, Any], mode: str) -> Optional[float]:
    keys = {
        "finish3d": "finish3d_stepover_percent",
        "rough3d": "rough3d_stepover_percent",
        "pocket": "pocket_stepover_percent",
    }
    key = keys.get(mode)
    return None if key is None else _number(settings, key)


def analyze_cam_settings(
    settings: Mapping[str, Any],
    contours: Optional[Sequence[Sequence[Sequence[float]]]] = None,
) -> CAMAdviceReport:
    """Analyze a valid CAM setup without mutating it.

    ``contours`` is optional because 3D operations use a mesh source. Geometry
    advice is intentionally limited to facts that can be inferred safely.
    """

    items = []
    mode = str(settings.get("operation_mode", "") or "")
    tool_type = str(settings.get("tool_type", "end_mill") or "end_mill")
    diameter = _number(settings, "tool_diameter")
    stepdown = _number(settings, "stepdown")
    safe_height = _number(settings, "safe_height")
    retract_height = _number(settings, "retract_height")
    thickness = _number(settings, "material_thickness")
    cut_depth = _number(settings, "cut_depth")

    if tool_type == "drill" and mode not in {"holes"}:
        items.append(
            CAMAdvice(
                "drill-side-cut",
                "warning",
                "Broca escolhida para usinagem lateral",
                "Brocas são destinadas à furação axial. Revise a ferramenta antes de cortar contornos, bolsões ou superfícies.",
            )
        )
    if mode == "finish3d" and tool_type != "ball_nose":
        items.append(
            CAMAdvice(
                "finish-tool",
                "recommendation",
                "Acabamento 3D sem fresa esférica",
                "Uma fresa esférica normalmente acompanha relevos com transições mais suaves e marcas menos agressivas.",
            )
        )
    if mode == "holes" and tool_type not in {"drill", "end_mill"}:
        items.append(
            CAMAdvice(
                "hole-tool",
                "warning",
                "Ferramenta incomum para furação",
                "Confirme diâmetro e geometria da ferramenta; o percurso de furo pressupõe broca ou fresa de topo apta a mergulhar.",
            )
        )

    if diameter > 0.0 and stepdown > diameter and tool_type in {
        "end_mill", "ball_nose", "compression"
    }:
        items.append(
            CAMAdvice(
                "deep-stepdown",
                "warning",
                "Profundidade por passe maior que o diâmetro",
                "Esse passe pode exigir esforço elevado. Confirme a recomendação do fabricante, a rigidez da máquina e o material.",
                "stepdown",
                stepdown,
                diameter,
            )
        )

    stepover = _finish_stepover(settings, mode)
    if mode == "finish3d" and stepover is not None and stepover > 15.0:
        items.append(
            CAMAdvice(
                "finish-stepover-high",
                "recommendation",
                "Passo lateral alto para acabamento fino",
                "Marcas entre passadas tendem a ficar mais visíveis. Para relevo detalhado, revise algo entre 8% e 12%.",
                "finish3d_stepover_percent",
                stepover,
                10.0,
            )
        )
    elif mode == "finish3d" and stepover is not None and stepover < 5.0:
        items.append(
            CAMAdvice(
                "finish-stepover-low",
                "info",
                "Acabamento muito denso",
                "A qualidade pode aumentar, mas o tempo e a quantidade de movimentos crescem bastante.",
                "finish3d_stepover_percent",
                stepover,
            )
        )
    if mode == "rough3d" and stepover is not None and stepover > 60.0:
        items.append(
            CAMAdvice(
                "rough-stepover-high",
                "warning",
                "Passo lateral agressivo no desbaste 3D",
                "Pode sobrar material entre passadas e aumentar a carga. Revise o limite recomendado para a fresa.",
                "rough3d_stepover_percent",
                stepover,
                40.0,
            )
        )
    if mode == "pocket" and stepover is not None and stepover > 60.0:
        items.append(
            CAMAdvice(
                "pocket-stepover-high",
                "warning",
                "Passo lateral agressivo no preenchimento",
                "O engajamento lateral está alto; confirme carga, evacuação de cavaco e potência disponível.",
                "pocket_stepover_percent",
                stepover,
                40.0,
            )
        )

    if safe_height < 3.0:
        items.append(
            CAMAdvice(
                "safe-height-low",
                "warning",
                "Altura segura com pouca margem",
                "Grampos, empeno da chapa e irregularidades podem superar essa folga. Confirme a montagem real.",
                "safe_height",
                safe_height,
                5.0,
            )
        )
    if retract_height - safe_height < 2.0:
        items.append(
            CAMAdvice(
                "retract-margin-low",
                "recommendation",
                "Pouca diferença entre retração e altura segura",
                "Uma margem maior torna os deslocamentos longos mais conservadores, especialmente com fixações altas.",
                "retract_height",
                retract_height,
                safe_height + 5.0,
            )
        )

    if mode == "cut" and thickness > 0.0:
        if cut_depth + 1e-6 < thickness:
            items.append(
                CAMAdvice(
                    "cut-not-through",
                    "warning",
                    "Corte não atravessa a espessura informada",
                    "Se a intenção for recortar a peça, ainda restará material no fundo. Para gravação ou sulco, ignore esta observação.",
                    "cut_depth",
                    cut_depth,
                    thickness,
                )
            )
        elif cut_depth > thickness + 2.0:
            items.append(
                CAMAdvice(
                    "cut-too-deep",
                    "warning",
                    "Corte avança muito abaixo da chapa",
                    "Revise a profundidade extra para não consumir desnecessariamente a mesa de sacrifício.",
                    "cut_depth",
                    cut_depth,
                    thickness + 0.5,
                )
            )

    if contours and diameter > 0.0 and mode in {"holes", "pocket"}:
        narrow = 0
        for contour in contours:
            if len(contour) < 3:
                continue
            xs = [float(point[0]) for point in contour]
            ys = [float(point[1]) for point in contour]
            if min(max(xs) - min(xs), max(ys) - min(ys)) + 1e-6 < diameter:
                narrow += 1
        if narrow:
            items.append(
                CAMAdvice(
                    "tool-larger-than-feature",
                    "warning",
                    "Fresa maior que %d região(ões) selecionada(s)" % narrow,
                    "Essas regiões podem desaparecer, ser alargadas ou não gerar percurso. Confira a prévia exata.",
                )
            )

    items.sort(key=lambda item: (_LEVEL_ORDER[item.level], item.code))
    return CAMAdviceReport(tuple(items))


__all__ = ["CAMAdvice", "CAMAdviceReport", "analyze_cam_settings"]
