"""Architecture selection must fail before creating staging output."""
import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'ort_stage', Path(__file__).resolve().parents[1] / 'packaging/onnxruntime/stage_debian.py')
STAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STAGE)


class ArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = argparse.Namespace(architecture='arm64', output=self.root / 'output')

    def test_foreign_host_refuses_before_output(self):
        with patch.object(STAGE.subprocess, 'check_output', return_value='amd64\n'):
            with self.assertRaisesRegex(ValueError, 'requires native Debian arm64'):
                STAGE.stage(self.args)
        self.assertFalse(self.args.output.exists())

    def test_arm64_does_not_fall_back_to_amd64_lock(self):
        (self.root / 'stage-inputs.json').write_text(json.dumps({'architecture': 'amd64'}))
        with patch.object(STAGE, 'HERE', self.root), patch.object(
                STAGE.subprocess, 'check_output', return_value='arm64\n'):
            with self.assertRaises(FileNotFoundError) as caught:
                STAGE.stage(self.args)
        self.assertEqual(Path(caught.exception.filename).name, 'stage-inputs-arm64.json')
        self.assertFalse(self.args.output.exists())

    def test_mislabeled_arm64_lock_refuses(self):
        (self.root / 'stage-inputs-arm64.json').write_text(json.dumps({'architecture': 'amd64'}))
        with patch.object(STAGE, 'HERE', self.root), patch.object(
                STAGE.subprocess, 'check_output', return_value='arm64\n'):
            with self.assertRaisesRegex(ValueError, 'input architecture differs'):
                STAGE.stage(self.args)
        self.assertFalse(self.args.output.exists())


if __name__ == '__main__':
    unittest.main()
