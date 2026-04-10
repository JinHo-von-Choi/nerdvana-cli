from nerdvana_cli.core.hooks import HookEngine, HookEvent, HookContext, HookResult


def test_register_and_fire():
    engine = HookEngine()
    called = []
    def on_start(ctx):
        called.append(ctx.event)
        return HookResult(allow=True)
    engine.register(HookEvent.SESSION_START, on_start)
    results = engine.fire(HookContext(event=HookEvent.SESSION_START))
    assert len(called) == 1
    assert len(results) == 1
    assert results[0].allow is True


def test_unregister():
    engine = HookEngine()
    def handler(ctx):
        return HookResult()
    engine.register(HookEvent.SESSION_START, handler)
    assert engine.has_handlers(HookEvent.SESSION_START)
    engine.unregister(HookEvent.SESSION_START, handler)
    assert not engine.has_handlers(HookEvent.SESSION_START)


def test_handler_exception_caught():
    engine = HookEngine()
    def bad(ctx):
        raise ValueError("boom")
    engine.register(HookEvent.SESSION_START, bad)
    results = engine.fire(HookContext(event=HookEvent.SESSION_START))
    assert len(results) == 0


def test_multiple_handlers():
    engine = HookEngine()
    order = []
    def first(ctx):
        order.append(1)
        return HookResult(message="first")
    def second(ctx):
        order.append(2)
        return HookResult(message="second")
    engine.register(HookEvent.BEFORE_TOOL, first)
    engine.register(HookEvent.BEFORE_TOOL, second)
    results = engine.fire(HookContext(event=HookEvent.BEFORE_TOOL))
    assert order == [1, 2]
    assert len(results) == 2


def test_deny_result():
    engine = HookEngine()
    def blocker(ctx):
        return HookResult(allow=False, message="blocked")
    engine.register(HookEvent.BEFORE_TOOL, blocker)
    results = engine.fire(HookContext(event=HookEvent.BEFORE_TOOL, tool_name="Bash"))
    assert results[0].allow is False
