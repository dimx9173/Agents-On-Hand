import importlib
import pathlib


def _reload_config(monkeypatch, raw_ids, dev_allow="0"):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", raw_ids)
    monkeypatch.setenv("AOH_DEV_ALLOW_ALL_USERS", dev_allow)
    import agents_on_hand.config as cfg
    importlib.reload(cfg)
    return cfg


def test_blank_whitelist_denies(monkeypatch):
    cfg = _reload_config(monkeypatch, "")
    assert cfg.is_user_allowed(123) is False


def test_malformed_raises(monkeypatch):
    try:
        _reload_config(monkeypatch, "123, abc, 456")
        raise AssertionError("should have raised")
    except ValueError as e:
        assert "abc" in str(e)


def test_dev_allow_bypass(monkeypatch):
    cfg = _reload_config(monkeypatch, "", dev_allow="1")
    assert cfg.is_user_allowed(999999) is True


def test_path_blank_denies(monkeypatch):
    _reload_config(monkeypatch, "123")
    monkeypatch.setenv("ALLOWED_ROOT_DIRS", "")
    import agents_on_hand.config as cfg2
    importlib.reload(cfg2)
    assert cfg2.is_path_allowed(pathlib.Path("/tmp")) is False


def test_restricted_decorator_denies(monkeypatch):
    _reload_config(monkeypatch, "999")
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from agents_on_hand.security import restricted

    calls: list[int] = []

    @restricted
    async def dummy(update, context):  # type: ignore[no-untyped-def]
        calls.append(1)

    update = MagicMock()
    update.effective_user.id = 123
    update.message = AsyncMock()
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    ctx = MagicMock()
    asyncio.run(dummy(update, ctx))
    assert calls == []
    update.effective_user.id = 999
    asyncio.run(dummy(update, ctx))
    assert calls == [1]
