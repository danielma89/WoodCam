"""Keep local projects and optional model weights out of the portable ZIP."""

from scripts.build_portable_package import _iter_package_files


def test_portable_files_exclude_local_artifacts(tmp_path):
    (tmp_path / "ui.py").write_text("# WoodCAM\n")
    (tmp_path / "project.FCStd").write_text("private drawing")
    (tmp_path / "notes.zip").write_text("private archive")
    (tmp_path / "model.onnx").write_text("optional model")
    (tmp_path / "experiments").mkdir()
    (tmp_path / "experiments" / "result.png").write_text("preview")
    (tmp_path / "resources").mkdir()
    (tmp_path / "resources" / "icon.svg").write_text("<svg/>")

    included = {str(relative) for _, relative in _iter_package_files(tmp_path)}

    assert included == {"ui.py", "resources/icon.svg"}
