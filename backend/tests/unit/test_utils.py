from app import utils


def test_generate_key_returns_unique_urlsafe_strings():
    first = utils.generate_key()
    second = utils.generate_key()

    assert isinstance(first, str)
    assert isinstance(second, str)
    assert first != second
    assert len(first) >= 32


def test_generate_moniker_uses_curated_word_lists(monkeypatch):
    choices = iter(["swift", "phoenix"])
    monkeypatch.setattr(utils.random, "choice", lambda _: next(choices))

    assert utils.generate_moniker() == "swiftphoenix"


def test_generate_cursor_color_uses_curated_palette(monkeypatch):
    monkeypatch.setattr(utils.random, "choice", lambda values: values[0])

    assert utils.generate_cursor_color() == utils.CURSOR_COLORS[0]
    assert utils.generate_cursor_color().startswith("#")
