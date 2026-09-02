"""Read-only timing probe for an Editor 2D geometry commit.

Pass an FCStd path containing ``WoodCAM2D_VectorDocument``.  By default the
source file is only read as a zip archive and timings run in a fresh in-memory
FreeCAD document.  ``--host-document`` also opens the complete FCStd to expose
the cost of host recompute, but closes it without saving.
"""

from __future__ import annotations

import sys
import time
import xml.etree.ElementTree as ET
import zipfile
import os
from pathlib import Path

import FreeCAD


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from woodcam_editor.adapters.freecad_commands import FreeCADCommandSession  # noqa: E402
from woodcam_editor.adapters.freecad_store import FreeCADDocumentStore  # noqa: E402
from woodcam_editor.domain.commands import AddEntitiesCommand  # noqa: E402
from woodcam_editor.domain.entities import PathEntity  # noqa: E402
from woodcam_editor.domain.primitives import Vec2  # noqa: E402
from woodcam_editor.domain.serialization import deserialize_document  # noqa: E402


script_name = Path(__file__).name
probe_args = [
    argument
    for argument in sys.argv[1:]
    if Path(argument).name != script_name
]
if len(probe_args) != 1:
    raise SystemExit(
        "uso: FreeCADCmd run_commit_performance_probe.py arquivo.FCStd "
        "(WOODCAM_PROBE_HOST=1 mede também o documento hospedeiro)"
    )

source = Path(probe_args[0]).resolve()
with zipfile.ZipFile(str(source), "r") as archive:
    xml_root = ET.fromstring(archive.read("Document.xml"))
geometry_property = next(
    value
    for value in xml_root.iter("Property")
    if value.get("name") == "GeometryJSON"
)
geometry_json = geometry_property.find("String").get("value")

started = time.perf_counter()
vector_document = deserialize_document(geometry_json)
deserialize_elapsed = time.perf_counter() - started

host_document = os.environ.get("WOODCAM_PROBE_HOST", "").strip() == "1"
if host_document:
    document = FreeCAD.openDocument(str(source))
    store = FreeCADDocumentStore(document)
    vector_document = store.load()
    initial_save_elapsed = 0.0
else:
    document = FreeCAD.newDocument("WoodCAMCommitPerformanceProbe")
    store = FreeCADDocumentStore(document)
    started = time.perf_counter()
    store.save(vector_document, use_transaction=False)
    initial_save_elapsed = time.perf_counter() - started

entity = PathEntity.from_points(
    vector_document.active_layer_id,
    (Vec2(0.0, 0.0), Vec2(100.0, 100.0)),
    closed=False,
)
session = FreeCADCommandSession(vector_document, store)
started = time.perf_counter()
session.execute(AddEntitiesCommand((entity,)))
commit_elapsed = time.perf_counter() - started

print(
    "WoodCAM commit probe: %d entities; deserialize %.6f s; "
    "initial save %.6f s; add-one commit %.6f s"
    % (
        len(vector_document.entities_by_id),
        deserialize_elapsed,
        initial_save_elapsed,
        commit_elapsed,
    )
)
FreeCAD.closeDocument(document.Name)
