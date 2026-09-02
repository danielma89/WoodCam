import unittest

from woodcam_editor.application.common_line import CommonLineContour, plan_common_line_cut
from woodcam_editor.geometry.polygon_offset import round_offset_closed_polygon


class PolygonOffsetRegressionTests(unittest.TestCase):
    def test_four_millimetre_tool_trims_portigo_relief_offset_loops(self):
        # Exact first rounded recess from path-31ed87c7... in portigo.FCStd,
        # closed with a small representative section of the original rail.
        # Its 1.51 mm root radius is smaller than the 2 mm cutter radius.
        source = (
            (7.96132, 0.0),
            (7.83875, -0.123),
            (7.53192, -0.322),
            (7.19037, -0.453),
            (6.82902, -0.510),
            (6.463677, -0.491),
            (6.110293, -0.396),
            (5.784319, -0.230),
            (5.5, 0.0),
            (5.269763, 0.284),
            (5.103671, 0.610),
            (5.008982, 0.964),
            (4.989835, 1.329),
            (5.047067, 1.690),
            (5.178175, 2.032),
            (5.377431, 2.339),
            (5.5, 2.461),
            (5.5, 16.039),
            (5.377431, 16.161),
            (5.178175, 16.468),
            (5.047067, 16.810),
            (4.989835, 17.171),
            (5.008982, 17.536),
            (5.103671, 17.890),
            (5.269763, 18.216),
            (5.5, 18.5),
            (5.784319, 18.730),
            (6.110293, 18.896),
            (6.463677, 18.991),
            (6.82902, 19.010),
            (7.19037, 18.953),
            (7.53192, 18.822),
            (7.83875, 18.623),
            (7.96132, 18.5),
            (13.5, 18.5),
            (13.5, 30.0),
            (-8.0, 30.0),
            (-8.0, -12.0),
            (13.5, -12.0),
            (13.5, 0.0),
        )

        for label, oriented_source in (
            ("forward", source),
            ("reversed", tuple(reversed(source))),
        ):
            with self.subTest(orientation=label):
                compensated = round_offset_closed_polygon(oriented_source, 2.0)
                plan = plan_common_line_cut(
                    (CommonLineContour("portigo-relief", compensated),),
                    tolerance=1.0e-6,
                )

                self.assertTrue(plan.is_valid, plan.issues)
                self.assertGreater(len(compensated), len(source))


if __name__ == "__main__":
    unittest.main()
