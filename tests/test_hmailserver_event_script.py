# -*- coding: utf-8 -*-

import os
import subprocess
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "hmailserver"
    / "EventHandlers.example.vbs"
)


class HMailServerEventScriptTests(unittest.TestCase):
    def test_script_is_ascii_for_legacy_vbscript_hosts(self):
        SCRIPT_PATH.read_bytes().decode("ascii")

    def test_script_uses_classic_client_properties(self):
        script = SCRIPT_PATH.read_text(encoding="ascii")
        self.assertIn("oClient.Username", script)
        self.assertNotIn("oClient.Authenticated", script)
        self.assertNotIn("oClient.SessionID", script)

    @unittest.skipUnless(os.name == "nt", "Windows Script Host is only available on Windows")
    def test_windows_script_host_accepts_script(self):
        result = subprocess.run(
            ["cscript.exe", "//nologo", str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
            timeout=10,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
