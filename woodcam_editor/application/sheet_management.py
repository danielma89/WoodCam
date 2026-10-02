"""Plan work-size changes using the existing document command boundary."""
from copy import deepcopy
from dataclasses import replace

from woodcam_editor.domain import (
    Affine2D, Vec2, GroupEntity, CompositeCommand, SetWorkAreaCommand,
    SetDocumentMetadataCommand, ReplaceEntitiesCommand, DeleteEntitiesCommand,
)
from woodcam_editor.domain.sheets import sheet_bounds
from .piece_organizer import classify_document_pieces, organization_sheet_index


def resize_work_area_command(document, work_area, sheet_index=0):
    """Resize the active sheet, moving neighbours only to prevent overlap.

    Without stored sheets, retain the historical work-area-only operation.
    A layout resize invalidates its old remnant cuts; it never scales parts.
    """
    old = sheet_bounds(document)
    if work_area is None or not document.metadata.get('organization_sheet_bounds'):
        return SetWorkAreaCommand(work_area)
    width = work_area.max_x - work_area.min_x
    height = work_area.max_y - work_area.min_y
    if not 0 <= sheet_index < len(old):
        raise ValueError('Selecione uma chapa para editar.')
    new = list(old)
    active = old[sheet_index]
    new[sheet_index] = (active[0], active[1], active[0] + width, active[1] + height)
    # Preserve every other size; shift only right-hand neighbours reached by
    # the expanded sheet. Shrinking never pulls neighbouring sheets inward.
    placed = [sheet_index]
    for index in sorted((i for i in range(len(old)) if i != sheet_index), key=lambda i: old[i][0]):
        bounds = new[index]
        if bounds[0] < active[0]:
            continue
        x = bounds[0]
        for previous in placed:
            other = new[previous]
            if bounds[1] < other[3] and bounds[3] > other[1] and x < other[2] + 50.:
                x = other[2] + 50.
        new[index] = (x, bounds[1], x + bounds[2] - bounds[0], bounds[3])
        placed.append(index)
    commands = [SetWorkAreaCommand(work_area)]
    if tuple(new) == old:
        return commands[0]

    # Join all declared descendants and groups before assigning sheets. This
    # keeps holes, pockets and markings rigid even when the source overflows.
    leaves = {key: entity for key, entity in document.entities_by_id.items()
              if not isinstance(entity, GroupEntity)
              and entity.metadata.get('woodcam_role') != 'remnant_cut'}
    parent = {key: key for key in leaves}
    def root(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key
    def join(ids):
        ids = [key for key in ids if key in parent]
        if ids:
            first = root(ids[0])
            for key in ids[1:]:
                parent[root(key)] = first
    def children(key, visited=None):
        visited = set() if visited is None else visited
        if key in visited:
            return []
        visited.add(key)
        entity = document.entities_by_id.get(key)
        if isinstance(entity, GroupEntity):
            return [leaf for child in entity.child_ids for leaf in children(child, visited)]
        return [key] if key in leaves else []
    for entity in document.entities_by_id.values():
        if isinstance(entity, GroupEntity):
            ids = children(entity.id)
            sheets = set()
            for key in ids:
                box = leaves[key].bounds()
                sheets.add(organization_sheet_index(
                    (box.min_x, box.min_y, box.max_x, box.max_y), old))
            # A collection spanning sheets is not one physical piece.
            if len(sheets) == 1:
                join(ids)
    for piece in document.pieces_by_id.values():
        join((piece.outer_path_id,) + tuple(piece.inner_path_ids)
             + tuple(piece.metadata.get('pocket_path_ids', ()))
             + tuple(piece.metadata.get('marking_path_ids', ())))
    for piece in classify_document_pieces(document).pieces:
        join((piece.outer_id,) + tuple(piece.descendant_ids) + tuple(piece.feature_ids) + tuple(piece.marking_ids))
    groups = {}
    for key in leaves:
        groups.setdefault(root(key), []).append(key)
    replacements = []
    for ids in groups.values():
        boxes = [leaves[key].bounds() for key in ids]
        bounds = (min(b.min_x for b in boxes), min(b.min_y for b in boxes),
                  max(b.max_x for b in boxes), max(b.max_y for b in boxes))
        index = organization_sheet_index(bounds, old)
        delta = Vec2(new[index][0] - old[index][0], new[index][1] - old[index][1])
        if delta.x or delta.y:
            transform = Affine2D.translation(delta)
            replacements.extend(leaves[key].transformed(transform) for key in ids)
    remnant_ids = [key for key, entity in document.entities_by_id.items()
                   if entity.metadata.get('woodcam_role') == 'remnant_cut'
                   and int(entity.metadata.get('sheet_index', 0)) == sheet_index]
    if remnant_ids:
        commands.append(DeleteEntitiesCommand(remnant_ids))
    def shifted_metadata(value, index, point_keys, bounds_key):
        value = deepcopy(value)
        dx, dy = new[index][0] - old[index][0], new[index][1] - old[index][1]
        for key in point_keys:
            if key in value:
                value[key] = [value[key][0] + dx, value[key][1] + dy]
        if bounds_key in value:
            box = value[bounds_key]
            value[bounds_key] = [box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy]
        return value
    for entity in document.entities_by_id.values():
        if entity.metadata.get('woodcam_role') != 'remnant_cut':
            continue
        index = int(entity.metadata.get('sheet_index', 0))
        if index == sheet_index or not 0 <= index < len(old) or new[index] == old[index]:
            continue
        delta = Vec2(new[index][0] - old[index][0], new[index][1] - old[index][1])
        replacements.append(replace(entity.transformed(Affine2D.translation(delta)),
            metadata=shifted_metadata(entity.metadata, index, ('cut_start', 'cut_end'), 'remnant_bounds')))
    cuts = []
    for cut in document.metadata.get('organization_remnant_cuts', ()):
        index = int(cut.get('sheet_index', 0))
        if index == sheet_index:
            continue
        cuts.append(shifted_metadata(cut, index, ('start', 'end'), 'remnant_bounds')
                    if 0 <= index < len(old) else deepcopy(cut))
    if replacements:
        commands.append(ReplaceEntitiesCommand(replacements))
    commands.extend((
        SetDocumentMetadataCommand('organization_sheet_bounds', [list(b) for b in new]),
        SetDocumentMetadataCommand('organization_remnant_cuts', cuts),
    ))
    return CompositeCommand(commands, label='Atualizar tamanho das chapas')
