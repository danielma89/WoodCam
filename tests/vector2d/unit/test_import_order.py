import os
import subprocess
import sys
import unittest


class ImportOrderTests(unittest.TestCase):
    def _run(self, source):
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [sys.executable, "-B", "-c", source],
            cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")),
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_geometry_can_be_imported_before_domain(self):
        self._run(
            "from woodcam_editor.geometry import preview_offset_closed_path; "
            "from woodcam_editor.domain import PathEntity"
        )

    def test_domain_can_be_imported_before_geometry(self):
        self._run(
            "from woodcam_editor.domain import PathEntity; "
            "from woodcam_editor.geometry import preview_offset_closed_path"
        )


if __name__ == "__main__":
    unittest.main()

