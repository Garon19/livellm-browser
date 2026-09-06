import ast
from pathlib import Path


def test_main_reads_runtime_port_from_environment():
    """Kubernetes operator exposes the app health endpoint on port 9000."""
    tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert any(
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "getenv"
        and call.args
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value == "PORT"
        for call in calls
    )
