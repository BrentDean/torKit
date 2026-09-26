from __future__ import annotations

from contextlib import redirect_stderr
from io import StringIO
import subprocess
import unittest
from unittest.mock import patch

from torkit_host.model import TorKitError
from torkit_host.process import run


class ProcessTests(unittest.TestCase):
    def test_failed_command_redacts_private_key(self) -> None:
        result = subprocess.CompletedProcess(
            args=["example"],
            returncode=1,
            stdout="Private key: ABCDEFGHIJKLMNOP\nother output\n",
        )
        stderr = StringIO()

        with patch("torkit_host.process.subprocess.run", return_value=result):
            with redirect_stderr(stderr), self.assertRaises(TorKitError):
                run(["example", "command"])

        output = stderr.getvalue()
        self.assertIn("Private key: [REDACTED]", output)
        self.assertNotIn("ABCDEFGHIJKLMNOP", output)
        self.assertIn("other output", output)

    def test_unchecked_failure_is_returned(self) -> None:
        result = subprocess.CompletedProcess(
            args=["example"],
            returncode=7,
            stdout="failed",
        )

        with patch("torkit_host.process.subprocess.run", return_value=result):
            actual = run(["example"], check=False)

        self.assertIs(actual, result)


if __name__ == "__main__":
    unittest.main()
