import tempfile
import unittest
from pathlib import Path

from scanner_app.core.project import create_project


class ProjectTests(unittest.TestCase):
    def test_import_copies_supported_images_and_writes_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / "source"; source.mkdir(); (source / "a.JPG").write_bytes(b"x"); (source / "notes.txt").write_text("ignore")
            project, count = create_project(root / "output", source)
            self.assertEqual(count, 1)
            self.assertTrue((project / "source" / "original_images" / "a.JPG").exists())
            self.assertEqual(__import__("json").loads((project / "project.json").read_text())["image_count"], 1)

    def test_import_finds_images_in_subfolders(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / "source"; nested = source / "camera_01"; nested.mkdir(parents=True)
            (nested / "frame.png").write_bytes(b"x")
            project, count = create_project(root / "output", source)
            self.assertEqual(count, 1)
            self.assertTrue((project / "source" / "original_images" / "camera_01" / "frame.png").exists())


if __name__ == "__main__":
    unittest.main()
