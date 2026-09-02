"""Descoberta do WoodCAM 2D usado pela bancada PanelNest."""

import commands.open_woodcam2d as woodcam_bridge

from commands.open_woodcam2d import (
    OpenWoodCAM2DCommand,
    find_woodcam_root,
    woodcam_icon_path,
)


def test_bridge_finds_configured_woodcam_project(monkeypatch, tmp_path):
    for file_name in ("ui.py", "operations.py", "gcode_writer.py"):
        (tmp_path / file_name).write_text("# teste\n", encoding="utf-8")
    icon_dir = tmp_path / "Resources" / "icons"
    icon_dir.mkdir(parents=True)
    icon = icon_dir / "woodcam2d.svg"
    icon.write_text("<svg/>", encoding="utf-8")
    monkeypatch.setenv("WOODCAM2D_PATH", str(tmp_path))

    assert find_woodcam_root() == tmp_path.resolve()
    assert woodcam_icon_path() == str(icon.resolve())


def test_bridge_command_uses_panelnest_cam_label():
    resources = OpenWoodCAM2DCommand().GetResources()

    assert resources["MenuText"] == "WoodCAM 2D"
    assert "G-code" in resources["ToolTip"]


def test_bridge_uses_freecad_user_mod_directory_on_any_platform(monkeypatch, tmp_path):
    class FakeFreeCAD:
        @staticmethod
        def getUserAppDataDir():
            return str(tmp_path)

    monkeypatch.setattr(woodcam_bridge, "App", FakeFreeCAD())

    assert (tmp_path / "Mod" / "WoodCAM2D").resolve() in (
        woodcam_bridge._woodcam_root_candidates()
    )
