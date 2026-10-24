"""Owned service APIs survive activation order and clean up on every exit."""
import pytest
from test_plugins import harness,plugin_root,write_plugin


@pytest.mark.parametrize("fail",[False,True])
def test_owned_service_is_removed_after_unload_or_failed_activation(harness,fail):
    h = harness
    code = '''
class Plugin:
    def activate(self,ctx):
        ctx.register_service("profiles", {"marker": 42})
        FAIL
    def deactivate(self): pass
def create_plugin(): return Plugin()
'''.replace("FAIL","raise RuntimeError('activation failed')" if fail else "pass")
    write_plugin(h.root,"service",code=code)
    h.manager.startup()
    if not fail:
        assert h.services.service("fixture.service.profiles") == {"marker":42}
        assert h.manager.deactivate("fixture.service")
    with pytest.raises(LookupError):
        h.services.service("fixture.service.profiles")


def test_service_duplicate_and_closed_context_are_refused_without_losing_original(harness):
    h = harness
    write_plugin(h.root,"service")
    h.manager.startup()
    context = h.manager.records[0].context
    provider = object()
    context.register_service("profiles",provider)
    assert h.services.service("fixture.service.profiles") is provider
    with pytest.raises(ValueError):
        context.register_service("profiles",object())
    context.close()
    with pytest.raises(ValueError):
        context.register_service("new",object())
    with pytest.raises(LookupError):
        h.services.service("fixture.service.profiles")
