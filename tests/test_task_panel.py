from nerdvana_cli.core.task_state import TaskRegistry, TaskState, TaskStatus


def test_task_panel_import() -> None:
    from nerdvana_cli.ui.task_panel import TaskPanel
    from textual.widget import Widget
    assert issubclass(TaskPanel, Widget)


def test_task_panel_accepts_registry() -> None:
    from nerdvana_cli.ui.task_panel import TaskPanel
    reg = TaskRegistry()
    panel = TaskPanel(task_registry=reg)
    assert panel._registry is reg


def test_status_icon_mapping() -> None:
    from nerdvana_cli.ui.task_panel import _status_icon
    assert _status_icon(TaskStatus.RUNNING)   == "●"
    assert _status_icon(TaskStatus.COMPLETED) == "✓"
    assert _status_icon(TaskStatus.FAILED)    == "✗"
    assert _status_icon(TaskStatus.PENDING)   == "○"
    assert _status_icon(TaskStatus.KILLED)    == "⊘"


def test_render_row_format() -> None:
    from nerdvana_cli.ui.task_panel import _render_row
    t = TaskState(id="a1", description="Test task")
    t.status       = TaskStatus.RUNNING
    t.current_tool = "Bash"
    t.tokens_used  = 127
    row = _render_row(t)
    assert "●" in row
    assert "Test task" in row
    assert "Bash" in row
    assert "127" in row
