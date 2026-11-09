import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from data_mining import cli


class CliTests(unittest.TestCase):
    def test_validate_config_keeps_public_output(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["validate-config"])
        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue(), "Configuración válida: 24 BQ.\n")
