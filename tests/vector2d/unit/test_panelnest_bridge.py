from dataclasses import dataclass, field
from types import SimpleNamespace
import unittest

from woodcam_editor.adapters.panelnest import (
    PanelNestContract,
    PanelNestContractError,
    PanelNestExchangeItem,
    PanelNestExchangeResult,
    PanelPart,
    inspect_panelnest_contract,
    panel_part_to_native,
)


@dataclass
class NativePanelPart:
    part_id: str
    object_name: str
    label: str
    length_mm: float
    width_mm: float
    thickness_mm: float
    quantity: int = 1
    material: str = ""
    holes: list = field(default_factory=list)
    profile_points: list = field(default_factory=list)


@dataclass
class NativePanelPartWithInternals(NativePanelPart):
    inner_profile_loops: list = field(default_factory=list)


def fake_module(native_type=NativePanelPart):
    return SimpleNamespace(
        __name__="fake_panelnest",
        PanelPart=native_type,
        ensure_part_properties=lambda _obj: None,
        collect_parts=lambda objects, include_hidden=False: list(objects),
    )


def sample_part(*, with_inner=False):
    inner = (((12.0, 12.0), (18.0, 12.0), (18.0, 18.0), (12.0, 18.0)),) if with_inner else ()
    return PanelPart(
        id="piece-1",
        profile_points=((10.0, 10.0), (110.0, 10.0), (110.0, 60.0), (10.0, 60.0)),
        inner_profile_loops=inner,
        circular_holes=({"x": 30.0, "y": 35.0, "diameter_mm": 10.0, "depth_mm": 0.0},),
        name="Lateral",
        quantity=2,
        material="MDF 18",
        thickness=18.0,
        rotations_allowed=(0.0, 90.0),
    )


class PanelNestBridgePureTests(unittest.TestCase):
    def test_optional_dependency_can_be_explicitly_absent(self):
        contract = inspect_panelnest_contract(None)

        self.assertFalse(contract.available)
        self.assertFalse(contract.native_panel_part)
        self.assertFalse(contract.has_collect_parts)

    def test_contract_reports_the_real_model_limitation(self):
        contract = inspect_panelnest_contract(fake_module())

        self.assertTrue(contract.available)
        self.assertTrue(contract.native_panel_part)
        self.assertTrue(contract.has_ensure_part_properties)
        self.assertTrue(contract.has_collect_parts)
        self.assertFalse(contract.supports_inner_profile_loops)
        self.assertFalse(contract.has_direct_part_import)

    def test_native_conversion_normalizes_coordinates_and_preserves_hole(self):
        native = panel_part_to_native(
            sample_part(),
            object_name="WoodCAM2DPNPart001_001",
            panelnest_module=fake_module(),
        )

        self.assertEqual(native.part_id, "piece-1")
        self.assertEqual(native.object_name, "WoodCAM2DPNPart001_001")
        self.assertEqual(native.length_mm, 100.0)
        self.assertEqual(native.width_mm, 50.0)
        self.assertEqual(native.profile_points[0], (0.0, 0.0))
        self.assertEqual(native.holes[0]["x_mm"], 20.0)
        self.assertEqual(native.holes[0]["y_mm"], 25.0)
        self.assertEqual(native.holes[0]["depth_mm"], 18.0)
        self.assertEqual(native.quantity, 2)

    def test_native_conversion_never_silently_drops_inner_profiles(self):
        with self.assertRaisesRegex(PanelNestContractError, "não aceita recortes internos"):
            panel_part_to_native(
                sample_part(with_inner=True),
                object_name="part",
                panelnest_module=fake_module(),
            )

    def test_future_native_contract_receives_inner_profiles(self):
        native = panel_part_to_native(
            sample_part(with_inner=True),
            object_name="part",
            panelnest_module=fake_module(NativePanelPartWithInternals),
        )

        self.assertEqual(len(native.inner_profile_loops), 1)
        self.assertEqual(native.inner_profile_loops[0][0], (2.0, 2.0))

    def test_exchange_result_is_plain_serializable_data(self):
        result = PanelNestExchangeResult(
            mode="freecad_exchange",
            group_name="WoodCAM2DPanelNestExchange",
            document_uuid="document-1",
            source_revision=7,
            revision_token="abc",
            contract=PanelNestContract(available=False),
            items=(
                PanelNestExchangeItem(
                    part_id="piece-1",
                    object_names=("Part001", "Part002"),
                    label="Lateral",
                    quantity=2,
                    outer_point_count=4,
                    inner_profile_count=1,
                    circular_hole_count=1,
                    source_offset=(10.0, 20.0),
                    shape_volume_mm3=1000.0,
                ),
            ),
        )

        payload = result.to_dict()
        self.assertEqual(payload["items"][0]["object_names"], ["Part001", "Part002"])
        self.assertEqual(result.object_names, ("Part001", "Part002"))
        self.assertFalse(payload["contract"]["available"])


if __name__ == "__main__":
    unittest.main()
