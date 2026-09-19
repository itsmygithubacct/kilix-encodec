"""The export verifiers must run their policy check where a bundle is read.

`verify_48khz.py --self-test` pins `verify_artifact_policy()` itself, and
`verify_export.py --self-test` pins both its check and its call site. Nothing
pinned the 48 kHz *call site*: deleting `verify_artifact_policy(manifest)` from
`load_manifest()` left every control passing, and only the converter binding's
source digest caught it, which a change that re-pinned the binding would not.

This module is that control, and it lives here rather than in
`tools/verify_48khz.py` because the 48 kHz manifest records that file's sha256:
a control written inside it would move the recorded population. It records no
test module.

`load_manifest()` imports numpy, onnx and torch before it reads anything, so
the export environment is stubbed in `sys.modules` for the call. Nothing else
is faked: the planted bundle, its manifest bytes and the checks that run over
them are the real ones, and the model directory named does not exist, so a
refusal that names the artifact policy can only come from the policy check,
which runs before the checkpoint is opened.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "tools"))

import verify_48khz  # noqa: E402
from export_24khz import canonical_json  # noqa: E402

#: The values the 48 kHz export records, typed here and not read from the
#: checker, so a changed expectation fails the admission control.
ACCEPTED_POLICY = {
    "derived_artifact_publication": "owner-reserved",
    "model_delivery": "local-safetensors-only",
    "network_access": False,
    "release_qualified": False,
    "unsafe_pickle_loaded": False,
}
POLICY_REFUSED = "48 kHz artifact policy differs"
#: What a manifest whose policy was admitted fails on next: the planted
#: manifest carries no model block, and no model directory exists.
PAST_THE_POLICY = "48 kHz model revision differs"


def export_environment() -> dict[str, object]:
    """Stand-ins for the three modules load_manifest() imports first."""
    numpy = types.ModuleType("numpy")
    numpy.__version__ = "0.0.0-stub"
    onnx = types.ModuleType("onnx")
    onnx.__version__ = "0.0.0-stub"
    onnx.checker = types.SimpleNamespace(
        check_model=lambda *_args, **_kwargs: None
    )
    onnx.load = lambda *_args, **_kwargs: None
    torch = types.ModuleType("torch")
    torch.__version__ = "0.0.0-stub"
    return {"numpy": numpy, "onnx": onnx, "torch": torch}


class FortyEightKilohertzCallSiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._scratch = tempfile.TemporaryDirectory(
            prefix="kilix-encodec-policy-", dir=os.environ.get("TMPDIR")
        )
        self.scratch = Path(self._scratch.name)
        self.assertFalse(self.scratch.resolve().is_relative_to(ROOT))

    def tearDown(self) -> None:
        self._scratch.cleanup()

    def outcome(self, name: str, policy: object) -> str:
        """Run the real load_manifest() over a planted bundle; return its refusal."""
        bundle = self.scratch / name
        bundle.mkdir()
        manifest: dict[str, object] = {"schema": "kilix.encodec.48khz-export/v1"}
        if policy is not None:
            manifest["artifact_policy"] = policy
        (bundle / "manifest.json").write_bytes(canonical_json(manifest))
        model_directory = self.scratch / "model-directory-that-does-not-exist"
        self.assertFalse(model_directory.exists())
        with mock.patch.dict(sys.modules, export_environment()):
            try:
                verify_48khz.load_manifest(bundle, model_directory)
            except BaseException as error:  # any later failure counts as "admitted"
                return str(error)
        return "admitted with no refusal at all"

    def test_load_manifest_refuses_a_planted_artifact_policy(self) -> None:
        # Each plant must be refused by the policy check inside load_manifest().
        # With the call deleted, every one of them reaches the model checks
        # instead and this test fails.
        plants: list[tuple[str, object]] = [
            ("absent", None),
            ("empty", {}),
            ("release-qualified", {**ACCEPTED_POLICY, "release_qualified": True}),
            ("release-qualified-zero", {**ACCEPTED_POLICY, "release_qualified": 0}),
            ("release-qualified-zero-float",
             {**ACCEPTED_POLICY, "release_qualified": 0.0}),
            ("network-access-zero", {**ACCEPTED_POLICY, "network_access": 0}),
            ("unsafe-pickle-zero", {**ACCEPTED_POLICY, "unsafe_pickle_loaded": 0}),
            ("publication-reverted",
             {**ACCEPTED_POLICY, "derived_artifact_publication": "allowed"}),
            ("delivery-changed",
             {**ACCEPTED_POLICY, "model_delivery": "user-supplied-only"}),
            ("key-added", {**ACCEPTED_POLICY, "grant": "commercial"}),
            ("key-removed", {key: value for key, value in ACCEPTED_POLICY.items()
                             if key != "release_qualified"}),
        ]
        for name, policy in plants:
            with self.subTest(plant=name):
                self.assertEqual(self.outcome("planted-" + name, policy), POLICY_REFUSED)

    def test_the_recorded_policy_itself_passes_that_check(self) -> None:
        # Control: with the recorded values the bundle gets past the policy
        # check and fails on the next one, so the refusals above are the
        # policy check's own and not something the plants broke.
        outcome = self.outcome("accepted", dict(ACCEPTED_POLICY))
        self.assertEqual(outcome, PAST_THE_POLICY)

    def test_the_planted_manifest_is_the_real_shape(self) -> None:
        # The plants are refused for their policy, not for their form: the
        # manifest is canonical JSON with the schema the verifier expects.
        bundle = self.scratch / "shape"
        bundle.mkdir()
        manifest = {"artifact_policy": dict(ACCEPTED_POLICY),
                    "schema": "kilix.encodec.48khz-export/v1"}
        raw = canonical_json(manifest)
        (bundle / "manifest.json").write_bytes(raw)
        self.assertEqual(json.loads(raw), manifest)
        self.assertEqual(raw, canonical_json(json.loads(raw)))
        self.assertEqual(manifest["schema"], "kilix.encodec.48khz-export/v1")


if __name__ == "__main__":
    unittest.main()
