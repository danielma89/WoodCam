"""Conversão determinística de triângulos em uma superfície 2.5D superior."""

from __future__ import annotations

from dataclasses import dataclass
from collections import deque
import hashlib
import math
from typing import Iterable, Sequence


@dataclass(frozen=True)
class MeshData:
    vertices: tuple[tuple[float, float, float], ...]
    triangles: tuple[tuple[int, int, int], ...]
    source_id: str = ""

    @classmethod
    def create(cls, vertices, triangles, source_id=""):
        clean_vertices = tuple(
            (float(point[0]), float(point[1]), float(point[2]))
            for point in vertices
        )
        clean_triangles = tuple(
            (int(face[0]), int(face[1]), int(face[2])) for face in triangles
        )
        if len(clean_vertices) < 3 or not clean_triangles:
            raise ValueError("A malha 3D selecionada não contém triângulos suficientes.")
        return cls(clean_vertices, clean_triangles, str(source_id or ""))

    def fingerprint(self) -> str:
        digest = hashlib.sha256()
        digest.update(self.source_id.encode("utf-8", "replace"))
        for vertex in self.vertices:
            digest.update(("%.7f,%.7f,%.7f;" % vertex).encode("ascii"))
        for triangle in self.triangles:
            digest.update(("%d,%d,%d;" % triangle).encode("ascii"))
        return digest.hexdigest()


@dataclass(frozen=True)
class HeightField:
    """Grade regular contendo somente a pele superior alcançável no eixo Z."""

    origin_x: float
    origin_y: float
    step_x: float
    step_y: float
    columns: int
    rows: int
    heights: tuple[float | None, ...]
    source_min_z: float
    source_max_z: float
    source_hash: str = ""

    def __post_init__(self):
        if self.columns < 2 or self.rows < 2:
            raise ValueError("A superfície 3D precisa ter pelo menos 2 × 2 amostras.")
        if len(self.heights) != self.columns * self.rows:
            raise ValueError("A quantidade de alturas não corresponde à grade 3D.")
        if self.step_x <= 0.0 or self.step_y <= 0.0:
            raise ValueError("O passo da grade 3D precisa ser positivo.")

    @property
    def max_x(self):
        return self.origin_x + self.step_x * (self.columns - 1)

    @property
    def max_y(self):
        return self.origin_y + self.step_y * (self.rows - 1)

    @property
    def bounds(self):
        return self.origin_x, self.origin_y, self.max_x, self.max_y

    def index(self, column, row):
        return int(row) * self.columns + int(column)

    def value(self, column, row):
        if not (0 <= column < self.columns and 0 <= row < self.rows):
            return None
        return self.heights[self.index(column, row)]

    def point(self, column, row):
        return (
            self.origin_x + float(column) * self.step_x,
            self.origin_y + float(row) * self.step_y,
        )

    def sample(self, x, y):
        """Interpola a pele superior; fora da malha retorna ``None``."""
        fx = (float(x) - self.origin_x) / self.step_x
        fy = (float(y) - self.origin_y) / self.step_y
        if fx < -1e-8 or fy < -1e-8 or fx > self.columns - 1 + 1e-8 or fy > self.rows - 1 + 1e-8:
            return None
        fx = max(0.0, min(float(self.columns - 1), fx))
        fy = max(0.0, min(float(self.rows - 1), fy))
        c0 = int(math.floor(fx))
        r0 = int(math.floor(fy))
        c1 = min(self.columns - 1, c0 + 1)
        r1 = min(self.rows - 1, r0 + 1)
        candidates = []
        for column, row, weight in (
            (c0, r0, (1.0 - (fx - c0)) * (1.0 - (fy - r0))),
            (c1, r0, (fx - c0) * (1.0 - (fy - r0))),
            (c0, r1, (1.0 - (fx - c0)) * (fy - r0)),
            (c1, r1, (fx - c0) * (fy - r0)),
        ):
            value = self.value(column, row)
            if value is not None and weight > 0.0:
                candidates.append((value, weight))
        if not candidates:
            return None
        total = sum(weight for _value, weight in candidates)
        return sum(value * weight for value, weight in candidates) / total


def _barycentric_height(x, y, a, b, c):
    denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
    if abs(denominator) <= 1e-12:
        return None
    alpha = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / denominator
    beta = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / denominator
    gamma = 1.0 - alpha - beta
    tolerance = 1e-8
    if alpha < -tolerance or beta < -tolerance or gamma < -tolerance:
        return None
    return alpha * a[2] + beta * b[2] + gamma * c[2]


def _grid_size(span_x, span_y, sampling_mm, max_samples):
    requested = max(0.02, float(sampling_mm))
    columns = max(2, int(math.ceil(span_x / requested)) + 1)
    rows = max(2, int(math.ceil(span_y / requested)) + 1)
    count = columns * rows
    if count > int(max_samples):
        scale = math.sqrt(count / float(max_samples))
        requested *= scale
        columns = max(2, int(math.ceil(span_x / requested)) + 1)
        rows = max(2, int(math.ceil(span_y / requested)) + 1)
    return columns, rows


def height_field_from_grayscale(
    pixels,
    image_width,
    image_height,
    *,
    origin_x,
    origin_y,
    width_mm,
    height_mm,
    base_z,
    base_thickness,
    relief_height,
    sampling_mm=0.5,
    max_samples=300_000,
    source_hash="",
):
    """Cria a superfície CAM diretamente do mapa preservado do relevo.

    A imagem usa linha zero no topo; o domínio mecânico usa Y crescente para
    cima. Por isso as linhas são invertidas durante a amostragem. A função não
    cria nem percorre triângulos e mantém a precisão limitada pelo bitmap
    original, não pela malha visual do FreeCAD.
    """
    image_width = int(image_width)
    image_height = int(image_height)
    raw = bytes(pixels)
    if image_width < 2 or image_height < 2 or len(raw) != image_width * image_height:
        raise ValueError("O mapa de altura preservado está incompleto.")
    width_mm = float(width_mm)
    height_mm = float(height_mm)
    if width_mm <= 0.0 or height_mm <= 0.0:
        raise ValueError("O relevo não possui dimensões físicas válidas.")

    columns, rows = _grid_size(width_mm, height_mm, sampling_mm, max_samples)
    columns = min(columns, image_width)
    rows = min(rows, image_height)
    columns = max(2, columns)
    rows = max(2, rows)
    step_x = width_mm / float(columns - 1)
    step_y = height_mm / float(rows - 1)
    top_base = float(base_z) + float(base_thickness)
    amplitude = float(relief_height)

    def sample_pixel(source_x, source_y):
        x0 = int(math.floor(source_x))
        y0 = int(math.floor(source_y))
        x1 = min(image_width - 1, x0 + 1)
        y1 = min(image_height - 1, y0 + 1)
        tx = source_x - x0
        ty = source_y - y0
        p00 = raw[y0 * image_width + x0]
        p10 = raw[y0 * image_width + x1]
        p01 = raw[y1 * image_width + x0]
        p11 = raw[y1 * image_width + x1]
        return (
            p00 * (1.0 - tx) * (1.0 - ty)
            + p10 * tx * (1.0 - ty)
            + p01 * (1.0 - tx) * ty
            + p11 * tx * ty
        )

    heights = []
    for row in range(rows):
        source_y = (image_height - 1) * (1.0 - row / float(rows - 1))
        for column in range(columns):
            source_x = (image_width - 1) * column / float(columns - 1)
            gray = sample_pixel(source_x, source_y)
            heights.append(top_base + amplitude * gray / 255.0)
    return HeightField(
        float(origin_x),
        float(origin_y),
        step_x,
        step_y,
        columns,
        rows,
        tuple(heights),
        float(base_z),
        top_base + amplitude,
        str(source_hash or ""),
    )


def height_field_from_mesh(mesh: MeshData, sampling_mm=0.5, max_samples=300_000):
    """Projeta a malha de cima para baixo, preservando o maior Z em cada XY.

    Paredes verticais não formam área projetada e são ignoradas. Isso torna a
    limitação 2.5D explícita: reentrâncias/undercuts não são alcançáveis em uma
    única fixação de três eixos.
    """
    xs = [point[0] for point in mesh.vertices]
    ys = [point[1] for point in mesh.vertices]
    zs = [point[2] for point in mesh.vertices]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    if max_x - min_x <= 1e-9 or max_y - min_y <= 1e-9:
        raise ValueError("A malha selecionada não possui área útil no plano XY.")
    columns, rows = _grid_size(max_x - min_x, max_y - min_y, sampling_mm, max_samples)
    step_x = (max_x - min_x) / float(columns - 1)
    step_y = (max_y - min_y) / float(rows - 1)
    heights = [None] * (columns * rows)

    for triangle in mesh.triangles:
        try:
            a, b, c = (mesh.vertices[index] for index in triangle)
        except (IndexError, TypeError):
            continue
        tri_min_x, tri_max_x = min(a[0], b[0], c[0]), max(a[0], b[0], c[0])
        tri_min_y, tri_max_y = min(a[1], b[1], c[1]), max(a[1], b[1], c[1])
        c0 = max(0, int(math.floor((tri_min_x - min_x) / step_x)) - 1)
        c1 = min(columns - 1, int(math.ceil((tri_max_x - min_x) / step_x)) + 1)
        r0 = max(0, int(math.floor((tri_min_y - min_y) / step_y)) - 1)
        r1 = min(rows - 1, int(math.ceil((tri_max_y - min_y) / step_y)) + 1)
        for row in range(r0, r1 + 1):
            y = min_y + row * step_y
            base = row * columns
            for column in range(c0, c1 + 1):
                x = min_x + column * step_x
                z = _barycentric_height(x, y, a, b, c)
                if z is None:
                    continue
                index = base + column
                if heights[index] is None or z > heights[index]:
                    heights[index] = z

    if not any(value is not None for value in heights):
        raise ValueError("Não foi possível projetar a superfície superior da malha.")
    return HeightField(
        min_x,
        min_y,
        step_x,
        step_y,
        columns,
        rows,
        tuple(heights),
        min(zs),
        max(zs),
        mesh.fingerprint(),
    )


def extend_height_field(field, bounds, outside_height=None, max_samples=300_000):
    """Estende a pele até uma fronteira de material/vetor com fundo plano."""
    min_x, min_y, max_x, max_y = (float(value) for value in bounds)
    min_x, min_y = min(min_x, field.origin_x), min(min_y, field.origin_y)
    max_x, max_y = max(max_x, field.max_x), max(max_y, field.max_y)
    columns, rows = _grid_size(
        max_x - min_x,
        max_y - min_y,
        min(field.step_x, field.step_y),
        max_samples,
    )
    step_x = (max_x - min_x) / float(columns - 1)
    step_y = (max_y - min_y) / float(rows - 1)
    floor = field.source_min_z if outside_height is None else float(outside_height)
    heights = []
    for row in range(rows):
        y = min_y + row * step_y
        for column in range(columns):
            x = min_x + column * step_x
            sampled = field.sample(x, y)
            heights.append(floor if sampled is None else sampled)
    return HeightField(
        min_x, min_y, step_x, step_y, columns, rows, tuple(heights),
        field.source_min_z, field.source_max_z, field.source_hash,
    )


def expand_height_field_support(field, padding, outside_height=None):
    """Dilata a silhueta projetada sem preencher sua caixa retangular.

    Esta é a fronteira ``Modelo``: somente a vizinhança da pele alcançável é
    aberta para a fresa. Fronteiras de material e vetor continuam usando
    :func:`extend_height_field`, pois nelas toda a área interna deve existir.
    """
    padding = max(0.0, float(padding))
    if padding <= 1e-9:
        return field
    pad_columns = int(math.ceil(padding / field.step_x))
    pad_rows = int(math.ceil(padding / field.step_y))
    columns = field.columns + 2 * pad_columns
    rows = field.rows + 2 * pad_rows
    heights = [None] * (columns * rows)
    distances = [-1] * (columns * rows)
    queue = deque()
    for row in range(field.rows):
        for column in range(field.columns):
            value = field.value(column, row)
            if value is None:
                continue
            target_column = column + pad_columns
            target_row = row + pad_rows
            index = target_row * columns + target_column
            heights[index] = value
            distances[index] = 0
            queue.append((target_column, target_row))

    floor = field.source_min_z if outside_height is None else float(outside_height)
    max_distance = max(pad_columns, pad_rows)
    while queue:
        column, row = queue.popleft()
        distance = distances[row * columns + column]
        if distance >= max_distance:
            continue
        for dc, dr in ((-1, 0), (1, 0), (0, -1), (0, 1),
                       (-1, -1), (-1, 1), (1, -1), (1, 1)):
            next_column, next_row = column + dc, row + dr
            if not (0 <= next_column < columns and 0 <= next_row < rows):
                continue
            dx = max(0, abs(next_column - pad_columns) - field.columns + 1)
            dy = max(0, abs(next_row - pad_rows) - field.rows + 1)
            if math.hypot(dx * field.step_x, dy * field.step_y) > padding + 1e-9:
                continue
            index = next_row * columns + next_column
            if distances[index] >= 0:
                continue
            distances[index] = distance + 1
            heights[index] = floor
            queue.append((next_column, next_row))

    return HeightField(
        field.origin_x - pad_columns * field.step_x,
        field.origin_y - pad_rows * field.step_y,
        field.step_x,
        field.step_y,
        columns,
        rows,
        tuple(heights),
        field.source_min_z,
        field.source_max_z,
        field.source_hash,
    )
