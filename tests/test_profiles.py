import json
import os
from pathlib import Path

import pytest

from clickcue.models import Step, TargetWindow
from clickcue.profiles import Profile, ProfileStore


def make_profile(name="Game A - farming"):
    return Profile(
        name=name,
        steps=[Step("Button 1", 100, 200, 1.5),
               Step("Pause", 0, 0, 210, wait_only=True),
               Step("Button 2", 300, 400, 0.5)],
        loop_forever=False,
        repetitions=10,
        target_window=TargetWindow("Some Game", 40, 60, 1280, 720),
    )


def test_save_and_load_round_trip(tmp_path):
    store = ProfileStore(tmp_path)
    profile = make_profile()
    store.save(profile)

    loaded = store.load(profile.name)
    assert loaded == profile
    assert json.loads((tmp_path / f"{profile.name}.json").read_text())["schema_version"] == 1


def test_save_supports_long_filename_accepted_by_filesystem(tmp_path):
    # Discover the directory's actual path/component limit, including on Windows.
    for length in range(250, 0, -1):
        name = "p" * length
        path = tmp_path / f"{name}.json"
        try:
            path.write_text("{}", encoding="utf-8")
        except OSError:
            continue
        break
    else:
        pytest.fail("Could not create a profile file in the test directory")

    store = ProfileStore(tmp_path)
    profile = make_profile(name)
    store.save(profile)

    assert store.load(name) == profile
    assert list(tmp_path.iterdir()) == [path]


def test_list_names_and_delete(tmp_path):
    store = ProfileStore(tmp_path)
    store.save(make_profile("Alpha"))
    store.save(make_profile("Beta"))

    assert store.list_names() == ["Alpha", "Beta"]

    store.delete("Alpha")
    assert store.list_names() == ["Beta"]
    assert not store.exists("Alpha")


@pytest.mark.parametrize(("contents", "reason"), [
    (b"[]", "profile: must be an object"),
    (b"null", "profile: must be an object"),
    (b"\xff", "utf-8"),
    (b"{", "Expecting"),
    (b'{"name": 3}', "name"),
    (b'{"name": "Broken", "steps": [null]}', "steps[1]"),
    (b'{"name": "Other"}', "Other.json"),
])
def test_listing_isolates_invalid_files_without_changing_them(tmp_path, contents, reason):
    store = ProfileStore(tmp_path)
    store.save(make_profile("Alpha"))
    store.save(make_profile("Zulu"))
    broken = tmp_path / "Broken.json"
    broken.write_bytes(contents)

    assert store.list_names() == ["Alpha", "Zulu"]
    assert set(store.list_errors) == {"Broken.json"}
    assert reason in store.list_errors["Broken.json"]
    assert store.load("Alpha") == make_profile("Alpha")
    assert store.load("Zulu") == make_profile("Zulu")
    assert broken.read_bytes() == contents


def test_listing_recovers_from_unreadable_file(tmp_path, monkeypatch):
    store = ProfileStore(tmp_path)
    store.save(make_profile("Alpha"))
    store.save(make_profile("Zulu"))
    read_text = Path.read_text

    def deny_alpha(path, *args, **kwargs):
        if path.name == "Alpha.json":
            raise PermissionError("Access denied")
        return read_text(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", deny_alpha)
        assert store.list_names() == ["Zulu"]
        assert "Access denied" in store.list_errors["Alpha.json"]

    assert store.list_names() == ["Alpha", "Zulu"]
    assert store.list_errors == {}


def test_load_rejects_changed_name_even_when_filename_slug_matches(tmp_path):
    store = ProfileStore(tmp_path)
    store.save(make_profile("A/B"))
    assert store.list_names() == ["A/B"]
    store.save(make_profile("A?B"))

    with pytest.raises(ValueError, match="name"):
        store.load("A/B")


def test_save_overwrites_existing(tmp_path):
    store = ProfileStore(tmp_path)
    store.save(make_profile("Alpha"))

    updated = make_profile("Alpha")
    updated.steps = [Step("Only step", 1, 2, 0.1)]
    store.save(updated)

    assert store.load("Alpha").steps == updated.steps
    assert store.list_names() == ["Alpha"]


def test_failed_replacement_preserves_saved_profile(tmp_path, monkeypatch):
    store = ProfileStore(tmp_path)
    original = make_profile("Alpha")
    store.save(original)
    saved_bytes = (tmp_path / "Alpha.json").read_bytes()
    updated = make_profile("Alpha")
    updated.steps = []

    def deny_replace(source, destination):
        raise PermissionError("Profile is read-only")

    monkeypatch.setattr(os, "replace", deny_replace)
    with pytest.raises(PermissionError, match="read-only"):
        store.save(updated)

    assert (tmp_path / "Alpha.json").read_bytes() == saved_bytes
    assert store.load("Alpha") == original
    assert list(tmp_path.iterdir()) == [tmp_path / "Alpha.json"]


def test_filenames_are_sanitized(tmp_path):
    store = ProfileStore(tmp_path)
    name = "weird/name: with*chars?"
    store.save(make_profile(name))

    assert store.list_names() == [name]
    assert store.load(name).name == name
    assert all(path.parent == tmp_path for path in tmp_path.glob("*.json"))


def test_load_defaults_for_missing_fields(tmp_path):
    store = ProfileStore(tmp_path)
    (tmp_path / "Minimal.json").write_text(
        '{"name": "Minimal", "steps": '
        '[{"name": "Button", "x": 1, "y": 2, "delay": 0}]}')

    profile = store.load("Minimal")
    assert profile.loop_forever is True
    assert profile.repetitions == 1
    assert profile.target_window is None
    assert profile.steps == [Step("Button", 1, 2, 0, wait_only=False)]


def test_load_legacy_target_app(tmp_path):
    store = ProfileStore(tmp_path)
    (tmp_path / "Legacy.json").write_text(
        '{"name": "Legacy", "target_app": "Old Game", "steps": []}')

    profile = store.load("Legacy")
    assert profile.target_window == TargetWindow("Old Game")
    assert not profile.target_window.has_bounds


def test_target_window_bounds_helpers():
    saved = TargetWindow("Game", 0, 0, 100, 100)
    assert saved.has_bounds
    assert saved.same_bounds(TargetWindow("Game", 0, 0, 100, 100))
    assert not saved.same_bounds(TargetWindow("Game", 0, 0, 200, 100))
    assert not saved.same_bounds(TargetWindow("Game"))
    assert not TargetWindow("Game").has_bounds


@pytest.mark.parametrize(("data", "field"), [
    ([], "profile"),
    ({}, "name"),
    *[({"name": "Test", "schema_version": value}, "schema_version")
      for value in (-1, True, 1.0, "1", None, 2)],
    ({"name": " "}, "name"),
    ({"name": "Test", "steps": {}}, "steps"),
    ({"name": "Test", "steps": None}, "steps"),
    ({"name": "Test", "steps": [None]}, "steps\\[1\\]"),
    ({"name": "Test", "steps": [{}]}, "steps\\[1\\].name"),
    *[({"name": "Test", "loop_forever": value}, "loop_forever")
      for value in ("false", 0, None)],
    *[({"name": "Test", "repetitions": value}, "repetitions")
      for value in (0, -1, 1.5, True, "2", None)],
    *[({"name": "Test", "target_window": value}, "target_window")
      for value in ([], {}, "Game", {"app": " "},
                    {"app": "Game", "x": 0},
                    {"app": "Game", "x": 0, "y": 0, "width": 0, "height": 1},
                    {"app": "Game", "x": 0, "y": 0, "width": 1, "height": -1},
                    {"app": "Game", "x": 0.5, "y": 0, "width": 1, "height": 1},
                    {"app": "Game", "x": 0, "y": 0, "width": True, "height": 1},
                    {"app": "Game", "extra": 1})],
    ({"name": "Test", "target_app": " "}, "target_app"),
])
def test_load_rejects_invalid_profile_fields(tmp_path, data, field):
    (tmp_path / "Test.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match=field):
        ProfileStore(tmp_path).load("Test")


@pytest.mark.parametrize(("field", "value"), [
    ("name", " "), ("name", None),
    ("x", 1.5), ("x", True), ("x", "1"), ("y", None),
    ("delay", float("nan")), ("delay", float("inf")),
    ("delay", float("-inf")), ("delay", -0.1),
    ("delay", True), ("delay", "1"),
    ("delay", 10 ** 400),
    ("wait_only", "false"), ("wait_only", 0),
])
def test_load_rejects_invalid_step_fields(tmp_path, field, value):
    step = {"name": "Click", "x": -10, "y": 20, "delay": 0}
    step[field] = value
    data = {"name": "Test", "steps": [step]}
    (tmp_path / "Test.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match=rf"steps\[1\].{field}"):
        ProfileStore(tmp_path).load("Test")


def test_load_negative_coordinates_and_explicit_false():
    profile = Profile.from_dict({
        "name": "Secondary screen", "loop_forever": False,
        "target_window": {"app": "Game", "x": -100, "y": -20,
                          "width": 100, "height": 50},
        "steps": [{"name": "Click", "x": -10, "y": -5, "delay": 0,
                   "wait_only": False}],
    })
    assert profile.loop_forever is False
    assert profile.steps == [Step("Click", -10, -5, 0, False)]
    assert profile.target_window == TargetWindow("Game", -100, -20, 100, 50)


def test_load_name_only_profile_and_app_only_target():
    assert Profile.from_dict({"name": "Minimal"}) == Profile("Minimal")
    assert Profile.from_dict({
        "name": "App", "target_window": {"app": "Game", "x": None,
                                          "y": None, "width": None, "height": None},
    }).target_window == TargetWindow("Game")
