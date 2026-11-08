from typer.testing import CliRunner

from copydock import __version__
from copydock.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_package_rejects_bad_version_label(tmp_path):
    result = runner.invoke(app, ["package", str(tmp_path), "--app-version", "../evil"])
    assert result.exit_code == 1
    assert "Invalid --app-version" in result.output


def test_package_without_compose_file_fails_clearly(tmp_path):
    result = runner.invoke(app, ["package", str(tmp_path)])
    assert result.exit_code == 1
    assert "No Compose file" in result.output
