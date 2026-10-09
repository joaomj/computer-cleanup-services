"""External commands expose progress and terminate descendants at the deadline."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CommandProgressTest(unittest.TestCase):
    def test_progress_and_timeout_stop_descendants(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "descendant-finished"
            child = root / "child.py"
            child.write_text(
                "import time\nfrom pathlib import Path\ntime.sleep(2)\nPath("
                + repr(str(marker))
                + ').write_text("unexpected")\n'
            )
            wrapper = root / "wrapper.py"
            wrapper.write_text(
                "import subprocess,sys,time\nsubprocess.Popen([sys.executable, "
                + repr(str(child))
                + '])\nprint("visible progress", flush=True)\ntime.sleep(30)\n'
            )
            runner = root / "runner.py"
            runner.write_text(
                "from dataclasses import replace\nfrom config import load_config\nfrom common import run_command\nimport sys\nr=run_command([sys.executable,"
                + repr(str(wrapper))
                + "],replace(load_config(),command_timeout_seconds=1))\nprint(r[0])\n"
            )
            result = subprocess.run(
                [sys.executable, str(runner)],
                env={
                    **os.environ,
                    "PYTHONPATH": str(ROOT / "src"),
                    "XDG_CONFIG_HOME": str(root / "config"),
                },
                capture_output=True,
                text=True,
                timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "124")
            self.assertIn("visible progress", result.stderr)
            self.assertIn("process group terminated", result.stderr)
            # Waiting in a separate short process gives any leaked descendant time to write.
            subprocess.run(
                [sys.executable, "-c", "import time; time.sleep(2)"],
                check=True,
                timeout=4,
            )
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
