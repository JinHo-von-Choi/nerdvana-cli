"""Verify anthropic_provider has all required imports."""
import ast


def test_anthropic_provider_imports_json():
    """json.loads is used at line 110 — json must be imported."""
    with open("nerdvana_cli/providers/anthropic_provider.py") as f:
        source = f.read()

    assert "json.loads" in source, "json.loads is used in the file"

    tree = ast.parse(source)
    import_names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                import_names.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            import_names.append(node.module)

    assert "json" in import_names, "json must be imported at module level"
