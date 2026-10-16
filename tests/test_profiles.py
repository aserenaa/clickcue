import os

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
