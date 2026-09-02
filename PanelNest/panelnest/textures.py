"""Gerenciamento de texturas de madeira para o PanelNest.

Texturas CC0 da ambientcg.com — baixadas sob demanda e cacheadas em disco.
Aplicadas via Coin3D (RootNode) no ViewObject do FreeCAD 1.x.
Um DocumentObserver reaaplica as texturas automaticamente após cada recompute.
"""

import os
import zipfile
import urllib.request
import ssl
import pathlib

_TEXTURE_DIR = pathlib.Path.home() / ".local" / "share" / "PanelNest" / "textures"
# Resolução do pack baixado de ambientCG. 1K (1024²) é visualmente equivalente
# a 4K em peças de móvel (que tileiam a textura a cada 1m), mas usa 16× menos
# VRAM (~5 MB vs ~85 MB por painel após mipmaps).
_AMBIENTCG_RESOLUTION = "1K"
_AMBIENTCG_URL = "https://ambientcg.com/get?file={texture_id}_{res}-PNG.zip"

# Mapa em memória: obj.Name → caminho do PNG (para o observer reaaplicar)
_texture_registry: dict = {}

# Tamanho real (mm) que a textura representa no mundo. Texturas ambientCG 1K
# tipicamente representam ~1m × 1m de superfície real. A textura é tileada
# (repetida) em peças maiores e cortada (sem deformar) em peças menores.
TEXTURE_REAL_SIZE_MM = 1000.0


# ---------------------------------------------------------------------------
# Diretório e cache em disco
# ---------------------------------------------------------------------------

def texture_dir() -> pathlib.Path:
    _TEXTURE_DIR.mkdir(parents=True, exist_ok=True)
    return _TEXTURE_DIR


def texture_path(texture_id: str):
    base = texture_dir() / texture_id
    if not base.exists():
        return None
    for f in sorted(base.iterdir()):
        name = f.name.lower()
        if name.endswith(".png") and "color" in name:
            return f
    for f in sorted(base.iterdir()):
        if f.suffix.lower() == ".png":
            return f
    return None


def is_texture_downloaded(texture_id: str) -> bool:
    return texture_path(texture_id) is not None


def download_texture(texture_id: str, progress_callback=None):
    if is_texture_downloaded(texture_id):
        return texture_path(texture_id)

    url = _AMBIENTCG_URL.format(texture_id=texture_id, res=_AMBIENTCG_RESOLUTION)
    zip_path = texture_dir() / f"{texture_id}.zip"
    dest_dir = texture_dir() / texture_id

    try:
        if progress_callback:
            progress_callback(f"Baixando {texture_id}...")

        def _reporthook(block_num, block_size, total_size):
            if progress_callback and total_size > 0:
                pct = min(100, block_num * block_size * 100 // total_size)
                progress_callback(f"Baixando {texture_id}... {pct}%")

        ssl_ctx = ssl.create_default_context()
        try:
            ssl_ctx.load_default_certs()
        except Exception:
            ssl_ctx = ssl._create_unverified_context()

        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_ctx))
        opener.addheaders = [("User-Agent", "PanelNest/1.0")]
        urllib.request.install_opener(opener)
        urllib.request.urlretrieve(url, zip_path, reporthook=_reporthook)

        dest_dir.mkdir(exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as z:
            # Coin3D só usa o mapa Color. Pular Normal/Displacement/Roughness/
            # blend/mtlx/usdc/tres economiza ~95% do espaço em disco.
            for member in z.namelist():
                low = member.lower()
                if low.endswith(".png") and "color" in low:
                    z.extract(member, dest_dir)
        zip_path.unlink(missing_ok=True)

        return texture_path(texture_id)

    except Exception as exc:
        if zip_path.exists():
            zip_path.unlink(missing_ok=True)
        err_msg = f"Erro ao baixar {texture_id}: {type(exc).__name__}: {exc}"
        if progress_callback:
            progress_callback(err_msg)
        try:
            import FreeCAD as App
            App.Console.PrintError(f"PanelNest textures: {err_msg}\n")
        except Exception:
            pass
        return None


def ensure_texture(texture_id: str, progress_callback=None):
    if is_texture_downloaded(texture_id):
        return texture_path(texture_id)
    return download_texture(texture_id, progress_callback)


# ---------------------------------------------------------------------------
# Aplicação via Coin3D
# ---------------------------------------------------------------------------

def _panel_face_dims_mm(obj):
    """Retorna (face_length_mm, face_width_mm) da face principal do painel.

    A face principal é a maior das três faces de uma caixa, ou seja, os dois
    eixos com maior extensão da bbox. Retorna None se não conseguir medir.
    """
    try:
        shape = getattr(obj, "Shape", None)
        if shape is None:
            return None
        bbox = shape.BoundBox
        dims = sorted(
            [float(bbox.XLength), float(bbox.YLength), float(bbox.ZLength)],
            reverse=True,
        )
        if dims[0] <= 0 or dims[1] <= 0:
            return None
        return dims[0], dims[1]
    except Exception:
        return None


def _apply_coin_texture(view_obj, png_path: str, rotated: bool = False, bbox_override=None, replace_mode: bool = False, face_dims_mm=None) -> bool:
    """Injeta SoTexture2 no RootNode do ViewObject via Coin3D (pivy).

    replace_mode=True: usa REPLACE (pixel substitui ShapeColor diretamente),
    útil para aplicar cores sólidas como textura 1x1.
    replace_mode=False (padrão): usa MODULATE (pixel × ShapeColor), correto
    para texturas de madeira coloridas pela cor do material.
    """
    try:
        from pivy import coin
    except ImportError:
        try:
            import FreeCAD as App
            App.Console.PrintWarning("PanelNest: pivy não disponível — textura não aplicada.\n")
        except Exception:
            pass
        return False

    root = getattr(view_obj, "RootNode", None)
    if root is None:
        return False

    try:
        _strip_coin_texture_nodes(view_obj)

        # No modo MODULATE o pixel da textura é multiplicado pelo ShapeColor.
        # Para que a textura sempre apareça com sua cor real (sem ser tingida
        # por cor base do material aplicada anteriormente), forçamos
        # ShapeColor=(1,1,1) — branco é neutro em MODULATE.
        if not replace_mode:
            try:
                if hasattr(view_obj, "ShapeColor"):
                    view_obj.ShapeColor = (1.0, 1.0, 1.0)
            except Exception:
                pass
            try:
                if hasattr(view_obj, "DiffuseColor"):
                    face_count = 1
                    try:
                        face_count = max(1, len(list(view_obj.DiffuseColor)))
                    except Exception:
                        pass
                    view_obj.DiffuseColor = tuple([(1.0, 1.0, 1.0)] * face_count)
            except Exception:
                pass

        tex = coin.SoTexture2()
        tex.filename.setValue(png_path)
        if replace_mode:
            tex.model.setValue(coin.SoTexture2.REPLACE)
        else:
            tex.model.setValue(coin.SoTexture2.MODULATE)
        try:
            tex.wrapS.setValue(coin.SoTexture2.REPEAT)
            tex.wrapT.setValue(coin.SoTexture2.REPEAT)
        except Exception:
            pass

        # Coordenadas UV: o ViewProvider do FreeCAD para Part::Box/Cut etc.
        # NÃO emite SoTextureCoordinate2 nas faces, então SoTexture2 sozinho
        # mapearia para um único texel = aparece como cor sólida. Usamos
        # SoTextureCoordinatePlane para projetar a textura nas duas maiores
        # extensões da bbox local do objeto. directionS/T são dimensionados
        # de modo que 1 unidade no objeto (mm) = 1/TEXTURE_REAL_SIZE_MM em UV
        # → a textura tileia a cada TEXTURE_REAL_SIZE_MM da geometria.
        coord_plane = None
        if not replace_mode:
            try:
                shape = getattr(view_obj.Object, "Shape", None) if hasattr(view_obj, "Object") else None
                bb = shape.BoundBox if shape is not None else None
                if bb is not None and bb.XLength > 0 and bb.YLength > 0 and bb.ZLength > 0:
                    axes = sorted(
                        [(bb.XLength, (1.0, 0.0, 0.0)),
                         (bb.YLength, (0.0, 1.0, 0.0)),
                         (bb.ZLength, (0.0, 0.0, 1.0))],
                        key=lambda p: p[0],
                        reverse=True,
                    )
                    inv = 1.0 / float(TEXTURE_REAL_SIZE_MM)
                    dir_s = tuple(c * inv for c in axes[0][1])
                    dir_t = tuple(c * inv for c in axes[1][1])
                    if rotated:
                        dir_s, dir_t = dir_t, dir_s
                    coord_plane = coin.SoTextureCoordinatePlane()
                    coord_plane.directionS.setValue(*dir_s)
                    coord_plane.directionT.setValue(*dir_t)
            except Exception:
                coord_plane = None

        # Inserir em ordem inversa: o primeiro insertChild(., 0) fica em [0]
        # depois do próximo. Queremos: [0]=plane, [1]=tex, [2]=xform (se houver).
        if rotated and coord_plane is None:
            # Sem coord_plane mas precisa girar veio: usa SoTexture2Transform
            import math
            xform = coin.SoTexture2Transform()
            xform.rotation.setValue(math.pi / 2.0)
            root.insertChild(xform, 0)
            root.insertChild(tex, 0)
        else:
            root.insertChild(tex, 0)

        if coord_plane is not None:
            root.insertChild(coord_plane, 0)
        return True
    except Exception as exc:
        try:
            import FreeCAD as App
            App.Console.PrintWarning(f"PanelNest Coin3D: {exc}\n")
        except Exception:
            pass
        return False


def apply_solid_color_as_texture(obj, color_rgb: tuple) -> bool:
    """Aplica um PNG 1x1 da cor sólida como textura em REPLACE mode.

    Usa o mesmo caminho do Coin3D que funciona para texturas de madeira,
    mas em REPLACE para que o pixel sólido substitua o ShapeColor
    diretamente. Efetivamente substitui qualquer textura PNG anterior
    por uma cor uniforme.
    """
    view = getattr(obj, "ViewObject", None)
    if view is None:
        return False
    png = ensure_solid_color_png(color_rgb)
    if not png:
        return False
    ok = _apply_coin_texture(view, png, rotated=False, replace_mode=True)
    obj_name = getattr(obj, "Name", None)
    if obj_name and obj_name in _texture_registry:
        del _texture_registry[obj_name]
    return ok


def _strip_coin_texture_nodes(view_obj):
    """Remove todos os nós SoTexture2/SoTexture2Transform do RootNode."""
    try:
        from pivy import coin
        root = getattr(view_obj, "RootNode", None)
        if root is None:
            return
        to_remove = []
        for i in range(root.getNumChildren()):
            child = root.getChild(i)
            if isinstance(child, (coin.SoTexture2, coin.SoTexture2Transform, coin.SoTextureCoordinatePlane)):
                to_remove.append(child)
        for node in to_remove:
            root.removeChild(node)
    except Exception:
        pass


_WHITE_PIXEL_PNG_PATH = None


def _build_solid_color_png_1x1(r: int, g: int, b: int) -> bytes:
    """Gera bytes de um PNG 1x1 com a cor RGB especificada (0-255)."""
    import struct
    import zlib
    sig = b"\x89PNG\r\n\x1a\n"

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        length = struct.pack(">I", len(data))
        crc = struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
        return length + chunk_type + data + crc

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = bytes([0, r & 0xFF, g & 0xFF, b & 0xFF])
    idat = zlib.compress(raw)
    return sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def ensure_solid_color_png(color_rgb: tuple) -> str:
    """Garante que existe um PNG 1x1 da cor especificada em disco.

    color_rgb: floats (r,g,b) entre 0.0–1.0.
    Retorna o caminho do arquivo PNG.
    """
    r = max(0, min(255, int(round(float(color_rgb[0]) * 255))))
    g = max(0, min(255, int(round(float(color_rgb[1]) * 255))))
    b = max(0, min(255, int(round(float(color_rgb[2]) * 255))))

    target_dir = texture_dir()
    path = target_dir / f"_panelnest_solid_{r:02x}{g:02x}{b:02x}.png"

    if not path.exists():
        try:
            path.write_bytes(_build_solid_color_png_1x1(r, g, b))
        except Exception:
            return ""

    return str(path)


def _ensure_white_pixel_png() -> str:
    """PNG 1x1 branco (neutro em MODULATE)."""
    global _WHITE_PIXEL_PNG_PATH
    if _WHITE_PIXEL_PNG_PATH and os.path.exists(_WHITE_PIXEL_PNG_PATH):
        return _WHITE_PIXEL_PNG_PATH
    path = ensure_solid_color_png((1.0, 1.0, 1.0))
    if path:
        _WHITE_PIXEL_PNG_PATH = path
    return path


def _remove_coin_texture(view_obj):
    """Remove nós SoTexture2/SoTexture2Transform do RootNode e insere
    um SoTexture2 1x1 branco via filename (MODULATE neutro)."""
    try:
        from pivy import coin
    except ImportError:
        return

    root = getattr(view_obj, "RootNode", None)
    if root is None:
        return

    try:
        to_remove = []
        for i in range(root.getNumChildren()):
            child = root.getChild(i)
            if isinstance(child, (coin.SoTexture2, coin.SoTexture2Transform, coin.SoTextureCoordinatePlane)):
                to_remove.append(child)
        for node in to_remove:
            root.removeChild(node)
    except Exception:
        pass

    try:
        white_png = _ensure_white_pixel_png()
        if white_png:
            blank = coin.SoTexture2()
            blank.filename.setValue(white_png)
            blank.model.setValue(coin.SoTexture2.MODULATE)
            root.insertChild(blank, 0)
    except Exception:
        pass


def _register_texture_for_hierarchy(obj, png_str, _depth=0, _visited=None):
    """Registra png_str para obj e toda a sua hierarquia (filhos e Body pai).

    Segue App::Link para que rotate_grain encontre a textura independente
    do nível de resolução da seleção do FreeCAD.
    """
    if _visited is None:
        _visited = set()
    obj_id = id(obj)
    if obj_id in _visited or _depth > 8:
        return
    _visited.add(obj_id)

    # Registrar o objeto (e seu link real)
    name = getattr(obj, "Name", None)
    if name:
        _texture_registry[name] = png_str

    # Seguir App::Link
    type_id = getattr(obj, "TypeId", "") or ""
    if type_id in ("App::Link", "App::LinkElement"):
        linked = getattr(obj, "LinkedObject", None)
        if linked is not None and linked is not obj:
            _register_texture_for_hierarchy(linked, png_str, _depth + 1, _visited)
        return

    # Descer para todos os filhos
    for child in getattr(obj, "OutList", []):
        _register_texture_for_hierarchy(child, png_str, _depth + 1, _visited)

    # Subir para o Body pai (quando obj é um Pad/Feature)
    if _depth == 0:
        for parent in getattr(obj, "InList", []):
            if getattr(parent, "TypeId", "") == "PartDesign::Body":
                _register_texture_for_hierarchy(parent, png_str, _depth + 1, _visited)
                break


def apply_texture_to_object(obj, texture_png_path) -> bool:
    """Aplica textura ao objeto via Coin3D e registra para reaaplicação após recompute.

    Respeita PanelNestGrainRotated para orientar o veio corretamente.
    """
    view = getattr(obj, "ViewObject", None)
    if view is None:
        return False

    png_str = str(texture_png_path)
    # PanelNestGrainRotated pode estar no Body pai (se obj é Pad)
    _prop_src = obj
    for _parent in getattr(obj, "InList", []):
        if getattr(_parent, "TypeId", "") == "PartDesign::Body":
            _prop_src = _parent
            break
    grain_rotated = bool(getattr(_prop_src, "PanelNestGrainRotated", False))
    dims = _panel_face_dims_mm(obj)
    ok = _apply_coin_texture(view, png_str, rotated=grain_rotated, face_dims_mm=dims)

    # Registrar em toda a hierarquia para que rotate_grain funcione
    # independente do nível de resolução da seleção do FreeCAD
    _register_texture_for_hierarchy(obj, png_str)

    if ok:
        _ensure_observer()

    return ok


def remove_texture_from_object(obj):
    view = getattr(obj, "ViewObject", None)
    if view:
        _remove_coin_texture(view)
        # Força o FreeCAD a reconstruir o scene graph do ViewProvider.
        # Toggle de Visibility recria nós Coin3D, descartando qualquer
        # textura ainda cacheada no pipeline OpenGL.
        try:
            was_visible = bool(getattr(view, "Visibility", True))
            view.Visibility = False
            view.Visibility = was_visible
        except Exception:
            pass
    obj_name = getattr(obj, "Name", None)
    if obj_name and obj_name in _texture_registry:
        del _texture_registry[obj_name]


def rotate_grain_on_object(obj, prop_target=None) -> bool:
    """Gira o veio da textura 90° (alterna entre normal e rotacionado).

    obj: objeto com ViewObject (onde a textura Coin3D está aplicada).
    prop_target: objeto onde PanelNestGrainRotated é salvo (pode ser o Body pai).
                 Se None, usa obj.
    Retorna True se a operação foi bem-sucedida.
    """
    view = getattr(obj, "ViewObject", None)
    if view is None:
        return False

    if prop_target is None:
        prop_target = obj

    # Ler estado atual e calcular novo
    currently_rotated = bool(getattr(prop_target, "PanelNestGrainRotated", False))
    new_rotated = not currently_rotated

    # Buscar textura no registry — tenta pelo obj e pelo prop_target
    obj_name = getattr(obj, "Name", None)
    prop_name = getattr(prop_target, "Name", None)
    png_str = _texture_registry.get(obj_name)
    if png_str is None:
        png_str = _texture_registry.get(prop_name)
    if png_str is None:
        return False

    dims = _panel_face_dims_mm(obj)
    ok = _apply_coin_texture(view, png_str, rotated=new_rotated, face_dims_mm=dims)
    if not ok:
        return False

    # Salvar estado apenas se a textura foi aplicada com sucesso
    try:
        prop_target.PanelNestGrainRotated = new_rotated
    except Exception:
        pass

    return True


# ---------------------------------------------------------------------------
# DocumentObserver — reaaplica texturas após recompute
# ---------------------------------------------------------------------------

def _ensure_observer():
    pass  # Observer removido — causava delay e conflito com fita de borda


def reapply_registered_textures(doc=None):
    """Reaplica todas as texturas registradas em _texture_registry.

    Útil depois de operações que recomputam o documento (criação de planilhas,
    etc.) e podem descartar os nós Coin3D do RootNode dos ViewObjects.
    """
    try:
        import FreeCAD as App_local
    except ImportError:
        return 0
    target_doc = doc or App_local.ActiveDocument
    if target_doc is None:
        return 0
    applied = 0
    for obj_name, png_str in list(_texture_registry.items()):
        obj = target_doc.getObject(obj_name)
        if obj is None:
            continue
        view = getattr(obj, "ViewObject", None)
        if view is None:
            continue
        rotated = False
        try:
            rotated = bool(getattr(obj, "PanelNestGrainRotated", False))
        except Exception:
            pass
        try:
            dims = _panel_face_dims_mm(obj)
            if _apply_coin_texture(view, png_str, rotated=rotated, face_dims_mm=dims):
                applied += 1
        except Exception:
            pass
    return applied


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------

def cleanup_disk_cache(target_resolution: int = 1024, progress_callback=None) -> dict:
    """Remove mapas PBR não usados e reduz Color.png à resolução alvo.

    Para cada pasta Wood* em texture_dir():
      - apaga *_NormalDX.png, *_NormalGL.png, *_Displacement.png,
        *_Roughness.png, *.blend, *.mtlx, *.tres, *.usdc (não usados pelo
        pipeline Coin3D atual);
      - se PIL estiver disponível e Color.png for maior que target_resolution,
        reescala in-place (preserva proporção, LANCZOS).

    Retorna dict com estatísticas: {'removed': N, 'resized': M, 'freed_mb': X}.
    """
    removed = 0
    resized = 0
    freed = 0
    try:
        from PIL import Image
        has_pil = True
    except ImportError:
        has_pil = False

    junk_suffixes = (
        "_normaldx.png", "_normalgl.png",
        "_displacement.png", "_roughness.png",
        ".blend", ".mtlx", ".tres", ".usdc",
    )

    for entry in texture_dir().iterdir():
        if not entry.is_dir():
            continue
        for f in entry.iterdir():
            low = f.name.lower()
            if any(low.endswith(s) for s in junk_suffixes):
                try:
                    freed += f.stat().st_size
                    f.unlink()
                    removed += 1
                except Exception:
                    pass
                continue
            if has_pil and low.endswith(".png") and "color" in low:
                try:
                    with Image.open(f) as im:
                        w, h = im.size
                        if max(w, h) > target_resolution:
                            scale = target_resolution / float(max(w, h))
                            new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
                            before = f.stat().st_size
                            im_resized = im.resize(new_size, Image.LANCZOS)
                            im_resized.save(f, optimize=True)
                            after = f.stat().st_size
                            freed += max(0, before - after)
                            resized += 1
                            if progress_callback:
                                progress_callback(f"{f.name}: {w}×{h} → {new_size[0]}×{new_size[1]}")
                except Exception as exc:
                    if progress_callback:
                        progress_callback(f"Falha ao reescalar {f.name}: {exc}")

    return {"removed": removed, "resized": resized, "freed_mb": freed // (1024 * 1024)}


def list_missing_textures() -> list:
    from panelnest.materials import MATERIALS
    missing = []
    for mat in MATERIALS:
        if mat.texture_id and not is_texture_downloaded(mat.texture_id):
            missing.append(mat.texture_id)
    return list(dict.fromkeys(missing))
