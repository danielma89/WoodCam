"""Undoable sheet management; sheet bounds stay in VectorDocument metadata."""
from copy import deepcopy
from dataclasses import replace

from .commands import Command, DocumentChangeSet
from .entities import GroupEntity


def sheet_bounds(document):
    stored = document.metadata.get('organization_sheet_bounds', ())
    if stored:
        return tuple(tuple(map(float, bounds)) for bounds in stored)
    area = document.work_area
    return ((area.min_x, area.min_y, area.max_x, area.max_y),) if area else ()


def _touches(bounds, entity):
    box = entity.bounds()
    return (box.max_x >= bounds[0] and box.min_x <= bounds[2]
            and box.max_y >= bounds[1] and box.min_y <= bounds[3])


class DeleteEmptySheetCommand(Command):
    label = 'Apagar chapa vazia'

    def __init__(self, sheet_index):
        super().__init__()
        self.sheet_index = int(sheet_index)

    def _mutate(self, document):
        bounds = list(sheet_bounds(document))
        index = self.sheet_index
        if not 0 <= index < len(bounds):
            raise ValueError('Selecione uma chapa para apagar.')
        if len(bounds) <= 1:
            raise ValueError('Mantenha ao menos uma chapa no documento.')
        for entity in document.entities_by_id.values():
            if isinstance(entity, GroupEntity) or entity.metadata.get('woodcam_role') == 'remnant_cut':
                continue
            if _touches(bounds[index], entity):
                raise ValueError('A chapa contém vetores. Mova as peças para outra chapa antes de apagá-la.')
        removed, changed = set(), set()
        for entity_id, entity in list(document.entities_by_id.items()):
            if entity.metadata.get('woodcam_role') != 'remnant_cut':
                continue
            old_index = int(entity.metadata.get('sheet_index', 0))
            if old_index == index:
                del document.entities_by_id[entity_id]
                removed.add(entity_id)
            elif old_index > index:
                metadata = deepcopy(entity.metadata)
                metadata['sheet_index'] = old_index - 1
                metadata['remnant_id'] = str(metadata.get('remnant_id', '')).replace(
                    'sheet-%d-' % old_index, 'sheet-%d-' % (old_index - 1), 1)
                document.entities_by_id[entity_id] = replace(entity, metadata=metadata)
                changed.add(entity_id)
        cuts = []
        for cut in document.metadata.get('organization_remnant_cuts', ()):
            old_index = int(cut.get('sheet_index', 0))
            if old_index == index:
                continue
            value = deepcopy(cut)
            if old_index > index:
                value['sheet_index'] = old_index - 1
                value['remnant_id'] = str(value.get('remnant_id', '')).replace(
                    'sheet-%d-' % old_index, 'sheet-%d-' % (old_index - 1), 1)
            cuts.append(value)
        bounds.pop(index)
        document.metadata['organization_sheet_bounds'] = [list(value) for value in bounds]
        if 'organization_remnant_cuts' in document.metadata:
            document.metadata['organization_remnant_cuts'] = cuts
        return DocumentChangeSet(removed=frozenset(removed), changed=frozenset(changed), work_area_changed=True)
