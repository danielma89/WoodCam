"""Pure bitmap-to-height-map processing and deterministic relief meshing.

Raster pixels deliberately do not enter ``VectorDocument``.  This module owns
the independent 2.5D contract: a processed grayscale field in which black is
the base and white is the maximum configured relief height.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import math
import json
from pathlib import Path
from typing import Tuple


@dataclass(frozen=True)
class ReliefOptions:
    width_mm: float = 200.0
    height_mm: float = 120.0
    relief_height_mm: float = 3.0
    base_thickness_mm: float = 3.0
    origin_x_mm: float = 0.0
    origin_y_mm: float = 0.0
    invert: bool = False
    auto_levels: bool = True
    contrast: float = 1.0
    gamma: float = 1.0
    smoothing_radius_px: float = 0.5
    relief_mode: str = "aspire_bitmap"
    detail_strength: float = 0.45
    form_smoothing_radius_px: float = 5.0
    structure_strength: float = 0.85
    edge_feather_radius_px: float = 3.0
    sculptural_volume_strength: float = 0.35
    compensate_height: bool = True
    background_threshold: int = 20
    profile_power: float = 0.75
    max_source_pixels: int = 1024
    mesh_resolution: int = 384

    def __post_init__(self) -> None:
        positive = {
            "largura": self.width_mm,
            "altura": self.height_mm,
            "altura do relevo": self.relief_height_mm,
        }
        for label, value in positive.items():
            if not math.isfinite(float(value)) or float(value) <= 0.0:
                raise ValueError("A %s precisa ser positiva." % label)
        if not math.isfinite(float(self.base_thickness_mm)) or self.base_thickness_mm < 0.0:
            raise ValueError("A espessura da base não pode ser negativa.")
        if not math.isfinite(float(self.contrast)) or self.contrast <= 0.0:
            raise ValueError("O contraste precisa ser positivo.")
        if not math.isfinite(float(self.gamma)) or self.gamma <= 0.0:
            raise ValueError("O gamma precisa ser positivo.")
        if not math.isfinite(float(self.smoothing_radius_px)) or self.smoothing_radius_px < 0.0:
            raise ValueError("A suavização não pode ser negativa.")
        if self.relief_mode not in {"aspire_bitmap", "photo_volume", "heightmap"}:
            raise ValueError("O modo de relevo precisa ser bitmap, foto ou mapa de altura.")
        if not 0.0 <= float(self.detail_strength) <= 1.0:
            raise ValueError("O detalhe da foto precisa ficar entre 0 e 1.")
        if (
            not math.isfinite(float(self.form_smoothing_radius_px))
            or self.form_smoothing_radius_px < 0.0
        ):
            raise ValueError("A suavização do corpo não pode ser negativa.")
        if not 0.0 <= float(self.structure_strength) <= 1.0:
            raise ValueError("A definição da forma precisa ficar entre 0 e 1.")
        if (
            not math.isfinite(float(self.edge_feather_radius_px))
            or self.edge_feather_radius_px < 0.0
        ):
            raise ValueError("O arredondamento da borda não pode ser negativo.")
        if not 0.0 <= float(self.sculptural_volume_strength) <= 1.0:
            raise ValueError("O volume escultórico precisa ficar entre 0 e 1.")
        if not 0 <= int(self.background_threshold) <= 254:
            raise ValueError("O limiar de fundo precisa ficar entre 0 e 254.")
        if not math.isfinite(float(self.profile_power)) or self.profile_power <= 0.0:
            raise ValueError("O arredondamento do volume precisa ser positivo.")
        if int(self.max_source_pixels) < 16:
            raise ValueError("A resolução do mapa precisa ter ao menos 16 pixels.")
        if int(self.mesh_resolution) < 16:
            raise ValueError("A resolução da malha precisa ter ao menos 16 pontos.")


@dataclass(frozen=True)
class HeightMapData:
    width_px: int
    height_px: int
    pixels: bytes
    options: ReliefOptions
    source_name: str
    source_sha256: str
    generator: str = "bitmap"
    generator_metadata_json: str = "{}"

    def __post_init__(self) -> None:
        if self.width_px < 2 or self.height_px < 2:
            raise ValueError("O mapa de altura precisa ter pelo menos 2 × 2 pixels.")
        if len(self.pixels) != self.width_px * self.height_px:
            raise ValueError("O tamanho do buffer não corresponde ao mapa de altura.")
        if not str(self.generator).strip():
            raise ValueError("O gerador do mapa de altura precisa ser identificado.")
        try:
            metadata = json.loads(str(self.generator_metadata_json or "{}"))
        except Exception as error:
            raise ValueError("Os metadados do gerador são inválidos.") from error
        if not isinstance(metadata, dict):
            raise ValueError("Os metadados do gerador precisam ser um objeto JSON.")

    def value(self, column: int, row: int) -> int:
        return self.pixels[row * self.width_px + column]


@dataclass(frozen=True)
class ReliefMesh:
    vertices: Tuple[Tuple[float, float, float], ...]
    triangles: Tuple[Tuple[int, int, int], ...]
    columns: int
    rows: int

    @property
    def facet_count(self) -> int:
        return len(self.triangles)


def _pillow_modules():
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    except ImportError as error:  # pragma: no cover - deployment dependency
        raise RuntimeError("Criar relevo por imagem requer Pillow.") from error
    return Image, ImageEnhance, ImageFilter, ImageOps


def _resampling(Image):
    return getattr(getattr(Image, "Resampling", Image), "LANCZOS")


def load_heightmap(path: str | Path, options: ReliefOptions) -> HeightMapData:
    """Load and process an image without modifying the source file."""

    Image, ImageEnhance, ImageFilter, ImageOps = _pillow_modules()
    source_path = Path(path)
    if not source_path.is_file():
        raise FileNotFoundError(str(source_path))
    raw = source_path.read_bytes()
    with Image.open(str(source_path)) as opened:
        rgba = opened.convert("RGBA")
    # RGB and opacity are deliberately processed separately.  Flattening an
    # RGBA cut-out over white turns its transparent canvas into maximum relief
    # (and, after inversion, can produce a rectangular crater).  Transparency
    # is part of the bitmap contract here: alpha zero is always the base.
    image = rgba.convert("RGB").convert("L")
    alpha = rgba.getchannel("A")
    maximum = int(options.max_source_pixels)
    if max(image.size) > maximum:
        scale = maximum / float(max(image.size))
        size = (
            max(2, int(round(image.width * scale))),
            max(2, int(round(image.height * scale))),
        )
        image = image.resize(size, _resampling(Image))
        alpha = alpha.resize(size, _resampling(Image))
    if options.auto_levels:
        image = ImageOps.autocontrast(image, mask=alpha)
    if abs(float(options.contrast) - 1.0) > 1e-12:
        image = ImageEnhance.Contrast(image).enhance(float(options.contrast))
    gamma = float(options.gamma)
    if abs(gamma - 1.0) > 1e-12:
        lookup = [
            max(0, min(255, int(round(255.0 * ((value / 255.0) ** gamma)))))
            for value in range(256)
        ]
        image = image.point(lookup)
    if options.invert:
        image = ImageOps.invert(image)
    try:
        from PIL import ImageChops

        image = ImageChops.multiply(image, alpha)
    except ImportError:  # pragma: no cover - Pillow was already imported above
        pass
    subject_mask = None
    smoothing_radius = float(options.smoothing_radius_px)
    if options.relief_mode == "aspire_bitmap":
        height_ratio = (
            max(1.0, float(options.relief_height_mm) / 3.0)
            if options.compensate_height
            else 1.0
        )
        root_ratio = math.sqrt(height_ratio)
        effective_detail = float(options.detail_strength) / height_ratio
        effective_structure = float(options.structure_strength) / root_ratio
        effective_volume = 1.0 - (
            (1.0 - float(options.sculptural_volume_strength)) / root_ratio
        )
        effective_form_radius = float(options.form_smoothing_radius_px) * root_ratio
        effective_edge_radius = float(options.edge_feather_radius_px) * root_ratio
        smoothing_radius *= root_ratio
        subject_mask = _edge_connected_subject_mask(
            image,
            threshold=int(options.background_threshold),
            feather_radius_px=effective_edge_radius,
        )
        image = _component_bitmap_image(
            image,
            detail_strength=effective_detail,
            form_smoothing_radius_px=effective_form_radius,
            structure_strength=effective_structure,
            sculptural_volume_strength=effective_volume,
            subject_mask=subject_mask,
            profile_power=float(options.profile_power),
        )
    elif options.relief_mode == "photo_volume":
        image = _photo_volume_image(
            image,
            detail_strength=float(options.detail_strength),
            background_threshold=int(options.background_threshold),
            profile_power=float(options.profile_power),
        )
    if smoothing_radius > 0.0:
        image = (
            _masked_component_smoothing(
                image,
                subject_mask,
                radius_px=smoothing_radius,
            )
            if subject_mask is not None
            else image.filter(ImageFilter.GaussianBlur(radius=smoothing_radius))
        )
    if subject_mask is not None:
        image = Image.composite(image, Image.new("L", image.size, 0), subject_mask)
    # Gaussian smoothing may bleed a foreground value into fully transparent
    # pixels.  Keep those pixels exactly on the base while retaining soft,
    # partially transparent antialiasing at the subject edge.
    opaque_or_edge = alpha.point(lambda value: 255 if value > 0 else 0)
    image = Image.composite(image, Image.new("L", image.size, 0), opaque_or_edge)
    return HeightMapData(
        image.width,
        image.height,
        image.tobytes(),
        options,
        source_path.name,
        sha256(raw).hexdigest(),
    )


def _masked_component_smoothing(image, subject_mask, *, radius_px: float):
    """Smooth a component without pulling its transparent base into the form.

    Aspire calls the equivalent post-process *Smooth Components* with
    transparency preservation.  A plain Gaussian blur mixes zero-height
    background into the subject and erodes its boundary.  Normalized masked
    convolution smooths only values belonging to the component; the original
    mask is applied once later by ``load_heightmap``.
    """

    Image, _ImageEnhance, ImageFilter, _ImageOps = _pillow_modules()
    radius = max(0.0, float(radius_px))
    if radius <= 0.0:
        return image
    try:
        from PIL import ImageChops
    except ImportError:  # pragma: no cover - Pillow is already required
        return image.filter(ImageFilter.GaussianBlur(radius=radius))
    weighted = ImageChops.multiply(image, subject_mask).filter(
        ImageFilter.GaussianBlur(radius=radius)
    )
    weights = subject_mask.filter(ImageFilter.GaussianBlur(radius=radius))
    numerator = weighted.tobytes()
    denominator = weights.tobytes()
    original = image.tobytes()
    output = bytearray(len(original))
    for index, weight in enumerate(denominator):
        output[index] = (
            max(0, min(255, int(round(numerator[index] * 255.0 / weight))))
            if weight > 0
            else original[index]
        )
    return Image.frombytes("L", image.size, bytes(output))


def _component_bitmap_image(
    image,
    *,
    detail_strength: float,
    form_smoothing_radius_px: float,
    structure_strength: float,
    sculptural_volume_strength: float,
    subject_mask,
    profile_power: float,
):
    """Compress high-frequency texture while retaining the broad form.

    Mapping a photograph's complete black-to-white range to several
    millimetres makes scales, fur and JPEG noise become spikes.  A blurred
    low-frequency field carries the body volume; only a controlled fraction
    of the original high-frequency signal returns as surface detail.
    """

    Image, _ImageEnhance, ImageFilter, _ImageOps = _pillow_modules()
    radius = max(0.0, float(form_smoothing_radius_px))
    if radius <= 0.0:
        return image
    body = image.filter(ImageFilter.GaussianBlur(radius=radius))
    middle = image.filter(
        ImageFilter.GaussianBlur(radius=max(0.75, radius / 3.0))
    )
    fine_weight = max(0.0, min(1.0, float(detail_strength)))
    middle_weight = max(0.0, min(1.0, float(structure_strength)))
    volume_weight = max(0.0, min(1.0, float(sculptural_volume_strength)))
    source_values = image.tobytes()
    middle_values = middle.tobytes()
    body_values = body.tobytes()
    volume_image = (
        _silhouette_volume_image(subject_mask, profile_power=profile_power)
        if volume_weight > 0.0
        else None
    )
    volume_values = volume_image.tobytes() if volume_image is not None else body_values
    output = bytearray(len(source_values))
    for index, source_value in enumerate(source_values):
        body_value = body_values[index]
        middle_value = middle_values[index]
        base_value = (
            body_value * (1.0 - volume_weight)
            + volume_values[index] * volume_weight
        )
        value = (
            base_value
            + middle_weight * (middle_value - body_value)
            + fine_weight * (source_value - middle_value)
        )
        output[index] = max(0, min(255, int(round(value))))
    return Image.frombytes("L", image.size, bytes(output))


def _silhouette_volume_image(subject_mask, *, profile_power: float):
    """Create a smooth dome inside the connected photographic silhouette."""

    Image, _ImageEnhance, _ImageFilter, _ImageOps = _pillow_modules()
    width, height = subject_mask.size
    mask_values = subject_mask.tobytes()
    inside = bytearray(1 if value > 0 else 0 for value in mask_values)
    count = sum(inside)
    if count == 0 or count >= int(width * height * 0.98):
        return None
    infinity = float(width + height + 4)
    distances = [infinity if value else 0.0 for value in inside]
    diagonal = math.sqrt(2.0)
    for row in range(height):
        offset = row * width
        for column in range(width):
            index = offset + column
            if not inside[index]:
                continue
            best = distances[index]
            if column > 0:
                best = min(best, distances[index - 1] + 1.0)
            if row > 0:
                best = min(best, distances[index - width] + 1.0)
                if column > 0:
                    best = min(best, distances[index - width - 1] + diagonal)
                if column + 1 < width:
                    best = min(best, distances[index - width + 1] + diagonal)
            distances[index] = best
    for row in range(height - 1, -1, -1):
        offset = row * width
        for column in range(width - 1, -1, -1):
            index = offset + column
            if not inside[index]:
                continue
            best = distances[index]
            if column + 1 < width:
                best = min(best, distances[index + 1] + 1.0)
            if row + 1 < height:
                best = min(best, distances[index + width] + 1.0)
                if column > 0:
                    best = min(best, distances[index + width - 1] + diagonal)
                if column + 1 < width:
                    best = min(best, distances[index + width + 1] + diagonal)
            distances[index] = best
    maximum = max(distances[index] for index, value in enumerate(inside) if value)
    if maximum <= 0.0:
        return None
    power = max(0.05, float(profile_power))
    output = bytearray(width * height)
    for index, is_inside in enumerate(inside):
        if not is_inside:
            continue
        normalized = min(1.0, distances[index] / maximum)
        dome = math.sin(normalized * math.pi * 0.5) ** power
        output[index] = max(0, min(255, int(round(255.0 * dome))))
    return Image.frombytes("L", (width, height), bytes(output))


def _edge_connected_subject_mask(
    image, *, threshold: int, feather_radius_px: float = 0.0
):
    """Separate only low pixels connected to the canvas boundary as background.

    A plain threshold incorrectly punches holes through dark eyes, scales and
    fur.  Flooding from the four edges keeps those enclosed details inside the
    component while still removing a near-black photographic/JPEG canvas.
    """

    from collections import deque

    Image, _ImageEnhance, _ImageFilter, _ImageOps = _pillow_modules()
    width, height = image.size
    values = image.tobytes()
    background = bytearray(width * height)
    pending = deque()

    def enqueue(column, row):
        index = row * width + column
        if not background[index] and values[index] <= threshold:
            background[index] = 1
            pending.append(index)

    for column in range(width):
        enqueue(column, 0)
        enqueue(column, height - 1)
    for row in range(height):
        enqueue(0, row)
        enqueue(width - 1, row)
    while pending:
        index = pending.popleft()
        row, column = divmod(index, width)
        if column > 0:
            enqueue(column - 1, row)
        if column + 1 < width:
            enqueue(column + 1, row)
        if row > 0:
            enqueue(column, row - 1)
        if row + 1 < height:
            enqueue(column, row + 1)
    feather = max(0.0, float(feather_radius_px))
    if feather <= 0.0:
        payload = bytes(0 if value else 255 for value in background)
        return Image.frombytes("L", (width, height), payload)

    infinity = float(width + height + 4)
    distances = [0.0 if value else infinity for value in background]
    diagonal = math.sqrt(2.0)
    for row in range(height):
        offset = row * width
        for column in range(width):
            index = offset + column
            if background[index]:
                continue
            best = distances[index]
            if column > 0:
                best = min(best, distances[index - 1] + 1.0)
            if row > 0:
                best = min(best, distances[index - width] + 1.0)
                if column > 0:
                    best = min(best, distances[index - width - 1] + diagonal)
                if column + 1 < width:
                    best = min(best, distances[index - width + 1] + diagonal)
            distances[index] = best
    for row in range(height - 1, -1, -1):
        offset = row * width
        for column in range(width - 1, -1, -1):
            index = offset + column
            if background[index]:
                continue
            best = distances[index]
            if column + 1 < width:
                best = min(best, distances[index + 1] + 1.0)
            if row + 1 < height:
                best = min(best, distances[index + width] + 1.0)
                if column > 0:
                    best = min(best, distances[index + width - 1] + diagonal)
                if column + 1 < width:
                    best = min(best, distances[index + width + 1] + diagonal)
            distances[index] = best
    payload = bytearray(width * height)
    for index, is_background in enumerate(background):
        if not is_background:
            payload[index] = max(
                0, min(255, int(round(255.0 * min(1.0, distances[index] / feather))))
            )
    return Image.frombytes("L", (width, height), bytes(payload))


def _photo_volume_image(
    image,
    *,
    detail_strength: float,
    background_threshold: int,
    profile_power: float,
):
    """Turn a foreground silhouette into a rounded volume, then restore detail.

    This is an optional WoodCAM experiment, not the direct bitmap conversion
    used by Aspire.  Distance to the silhouette boundary supplies a synthetic
    low-frequency volume; source luminance returns as controlled detail.
    """

    Image, _ImageEnhance, _ImageFilter, _ImageOps = _pillow_modules()
    width, height = image.size
    source = image.tobytes()
    threshold = int(background_threshold)
    mask = bytearray(1 if value > threshold else 0 for value in source)
    foreground_count = sum(mask)
    if foreground_count == 0 or foreground_count >= int(width * height * 0.97):
        return image

    infinity = float(width + height + 4)
    distances = [infinity if value else 0.0 for value in mask]
    diagonal = math.sqrt(2.0)
    for row in range(height):
        row_offset = row * width
        for column in range(width):
            index = row_offset + column
            if not mask[index]:
                continue
            best = distances[index]
            if column == 0 or row == 0:
                best = min(best, 1.0)
            if column > 0:
                best = min(best, distances[index - 1] + 1.0)
            if row > 0:
                best = min(best, distances[index - width] + 1.0)
                if column > 0:
                    best = min(best, distances[index - width - 1] + diagonal)
                if column + 1 < width:
                    best = min(best, distances[index - width + 1] + diagonal)
            distances[index] = best
    for row in range(height - 1, -1, -1):
        row_offset = row * width
        for column in range(width - 1, -1, -1):
            index = row_offset + column
            if not mask[index]:
                continue
            best = distances[index]
            if column + 1 == width or row + 1 == height:
                best = min(best, 1.0)
            if column + 1 < width:
                best = min(best, distances[index + 1] + 1.0)
            if row + 1 < height:
                best = min(best, distances[index + width] + 1.0)
                if column > 0:
                    best = min(best, distances[index + width - 1] + diagonal)
                if column + 1 < width:
                    best = min(best, distances[index + width + 1] + diagonal)
            distances[index] = best
    maximum = max(distances[index] for index, value in enumerate(mask) if value)
    if maximum <= 0.0:
        return image
    detail = max(0.0, min(1.0, float(detail_strength)))
    power = float(profile_power)
    output = bytearray(width * height)
    for index, inside in enumerate(mask):
        if not inside:
            continue
        normalized = min(1.0, distances[index] / maximum)
        volume = 255.0 * (math.sin(normalized * math.pi * 0.5) ** power)
        output[index] = max(
            0,
            min(255, int(round(volume * (1.0 - detail) + source[index] * detail))),
        )
    return Image.frombytes("L", (width, height), bytes(output))


def _heightmap_image(data: HeightMapData):
    Image, _ImageEnhance, _ImageFilter, _ImageOps = _pillow_modules()
    return Image.frombytes("L", (data.width_px, data.height_px), data.pixels)


def heightmap_png(data: HeightMapData) -> bytes:
    output = BytesIO()
    _heightmap_image(data).save(output, format="PNG", optimize=True)
    return output.getvalue()


def shaded_preview_png(data: HeightMapData, maximum_px: int = 420) -> bytes:
    """Create a fast shaded relief thumbnail without OpenGL/Qt dependencies."""

    Image, _ImageEnhance, _ImageFilter, _ImageOps = _pillow_modules()
    source = _heightmap_image(data)
    if max(source.size) > maximum_px:
        scale = maximum_px / float(max(source.size))
        source = source.resize(
            (max(2, int(source.width * scale)), max(2, int(source.height * scale))),
            _resampling(Image),
        )
    values = source.load()
    shaded = Image.new("RGB", source.size)
    target = shaded.load()
    # Light from upper-left. Height tint remains visible even on flat regions.
    z_scale = max(0.25, float(data.options.relief_height_mm) * 0.22)
    for row in range(source.height):
        upper = max(0, row - 1)
        lower = min(source.height - 1, row + 1)
        for column in range(source.width):
            left = max(0, column - 1)
            right = min(source.width - 1, column + 1)
            dx = (values[right, row] - values[left, row]) / 255.0 * z_scale
            dy = (values[column, lower] - values[column, upper]) / 255.0 * z_scale
            nx, ny, nz = -dx, dy, 1.0
            length = math.sqrt(nx * nx + ny * ny + nz * nz)
            light = max(0.15, (nx * -0.45 + ny * 0.45 + nz * 0.78) / length)
            height = values[column, row] / 255.0
            target[column, row] = (
                int(max(0, min(255, (65 + 150 * height) * light))),
                int(max(0, min(255, (82 + 142 * height) * light))),
                int(max(0, min(255, (105 + 105 * height) * light))),
            )
    output = BytesIO()
    shaded.save(output, format="PNG")
    return output.getvalue()


def build_relief_mesh(data: HeightMapData, max_grid: int | None = None) -> ReliefMesh:
    """Build a closed triangular relief mesh with a flat Z=0 underside."""

    Image, _ImageEnhance, _ImageFilter, _ImageOps = _pillow_modules()
    maximum = max(16, int(max_grid or data.options.mesh_resolution))
    image = _heightmap_image(data)
    if max(image.size) > maximum:
        scale = maximum / float(max(image.size))
        image = image.resize(
            (max(2, int(round(image.width * scale))), max(2, int(round(image.height * scale)))),
            _resampling(Image),
        )
    columns, rows = image.size
    pixels = image.load()
    options = data.options
    top = []
    for row in range(rows):
        y = options.origin_y_mm + options.height_mm * (1.0 - row / float(rows - 1))
        for column in range(columns):
            x = options.origin_x_mm + options.width_mm * column / float(columns - 1)
            z = options.base_thickness_mm + options.relief_height_mm * pixels[column, row] / 255.0
            top.append((x, y, z))
    vertices = top + [(x, y, 0.0) for x, y, _z in top]
    offset = columns * rows
    triangles = []
    for row in range(rows - 1):
        for column in range(columns - 1):
            a = row * columns + column
            b = a + 1
            c = a + columns
            d = c + 1
            triangles.extend(((a, c, b), (b, c, d)))
            triangles.extend(
                ((offset + a, offset + b, offset + c), (offset + b, offset + d, offset + c))
            )
    perimeter = []
    perimeter.extend(range(columns))
    perimeter.extend(row * columns + columns - 1 for row in range(1, rows))
    perimeter.extend((rows - 1) * columns + column for column in range(columns - 2, -1, -1))
    perimeter.extend(row * columns for row in range(rows - 2, 0, -1))
    for index, current in enumerate(perimeter):
        following = perimeter[(index + 1) % len(perimeter)]
        triangles.extend(
            ((current, offset + current, following), (following, offset + current, offset + following))
        )
    return ReliefMesh(tuple(vertices), tuple(triangles), columns, rows)


__all__ = [
    "HeightMapData",
    "ReliefMesh",
    "ReliefOptions",
    "build_relief_mesh",
    "heightmap_png",
    "load_heightmap",
    "shaded_preview_png",
]
