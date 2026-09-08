"""Public offline spec fallback: real CLI and JS candidate integration."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from tools.spec_seed import seed
from src.codexautoai_v2.spec_authoring import validate_spec

ROOT = Path(__file__).resolve().parents[2]


class TestOfflineSpec(unittest.TestCase):
    def test_unique_drafts_preserve_intent_and_validate(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp) / "vault"
            intent = "建立 3D 模型\n尺寸需可調整"
            first = seed(intent, vault)
            original = first.read_text(encoding="utf-8")
            second = seed(intent, vault)
            self.assertNotEqual(first, second)
            self.assertEqual(original, first.read_text(encoding="utf-8"))
            self.assertIn("> 建立 3D 模型\n> 尺寸需可調整", original)
            self.assertIn("未呼叫模型", original)
            self.assertEqual(validate_spec(original), [])

    def test_empty_intent_creates_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                seed(" \n", Path(temp) / "vault")
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_cli_uses_vault_and_prints_existing_path(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp) / "中文 空間"
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools/spec_seed.py"), "seed", "建立模型"],
                env={**os.environ, "SPEC_VAULT": str(vault)},
                capture_output=True, text=True, encoding="utf-8", timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            target = Path(result.stdout.strip())
            self.assertTrue(target.is_file())
            self.assertTrue(target.is_relative_to(vault))

    @unittest.skipUnless(shutil.which("node"), "Node required")
    def test_packaged_candidate_without_private_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            ext = Path(temp) / "extension"
            tool = ext / "framework/tools/spec_seed.py"
            tool.parent.mkdir(parents=True)
            shutil.copyfile(ROOT / "tools/spec_seed.py", tool)
            script = """
const sf = require(process.argv[1]);
const all = sf.candidates('spec-forge', process.argv[2]);
const offline = all.filter(c => c.kind.startsWith('offline'));
sf.trySeed(all, 'test', {}, (cmd, opts, cb) => {
  cb(new Error('unavailable'), '', 'missing');
}, (file, args, opts, cb) => {
  if (!args.includes('test')) throw new Error('intent missing from argv');
  cb(null, '/generated/spec.md\\n', '');
}).then(r => console.log(JSON.stringify({kinds:offline.map(c=>c.kind), result:r})));
"""
            result = subprocess.run(
                ["node", "-e", script, str(ROOT / "vscode-extension/specforge.js"), str(ext)],
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data["kinds"][0], "offline")
            self.assertTrue(data["result"]["ok"])
            self.assertEqual(data["result"]["kind"], "offline")
