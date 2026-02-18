import os
from runtime.error_policy import should_reraise


def test_should_reraise_env(monkeypatch):
    monkeypatch.delenv('FRBOT_STRICT_ERRORS', raising=False)
    monkeypatch.setenv('FRBOT_MODE', 'real')
    assert not should_reraise()
    monkeypatch.setenv('FRBOT_STRICT_ERRORS', '1')
    assert should_reraise()
    monkeypatch.setenv('FRBOT_MODE', 'mock')
    # mock mode should disable reraise even if flag set
    assert not should_reraise()


def test_broad_except_behavior(monkeypatch):
    from runtime import error_policy

    def fn():
        try:
            raise ValueError('boom')
        except Exception:
            if error_policy.should_reraise():
                raise
            return 'swallowed'

    monkeypatch.delenv('FRBOT_STRICT_ERRORS', raising=False)
    monkeypatch.setenv('FRBOT_MODE', 'real')
    # default: not strict -> swallowed
    assert fn() == 'swallowed'

    monkeypatch.setenv('FRBOT_STRICT_ERRORS', '1')
    monkeypatch.setenv('FRBOT_MODE', 'real')
    try:
        fn()
        raised = False
    except ValueError:
        raised = True
    assert raised
