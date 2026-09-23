import importlib.util
from pathlib import Path

import pytest

from ik_lifecycle.service_control import PairedSymlinks
from ik_lifecycle.models import LifecycleBlockedError


@pytest.mark.parametrize("failure", ["none", "startup", "restart", "state-write"])
def test_guarded_promotion_restart_rollback_and_state_preservation(tmp_path, failure):
    assert importlib.util.find_spec("ik_lifecycle.guarded_promotion"), (
        "guarded promotion must replace ad hoc pointer switching"
    )
    from ik_lifecycle.guarded_promotion import run_promotion

    releases = tmp_path / "releases"
    releases.mkdir()
    old = releases / "old"
    old.mkdir()
    new = releases / "new"
    new.mkdir()
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    profile = profiles / "old"
    profile.mkdir()
    (profile / "state.db").write_bytes(b"original")
    candidate = profiles / "new"
    pointers = PairedSymlinks(
        tmp_path / "release",
        tmp_path / "profile",
        tmp_path / "journal.json",
        allowed_release_root=releases,
        allowed_profile_root=profiles,
    )
    pointers.initialize(old, profile, 1)

    class Service:
        running = True
        opens = 0

        def preflight(self):
            from ik_lifecycle.service_control import ServicePreflight

            return ServicePreflight(
                self.running, "running" if self.running else "inactive"
            )

        def close(self):
            self.running = False

        def closed(self):
            return not self.running

        def open(self):
            self.running = True
            self.opens += 1

        def candidate_definitions(self):
            pass

        def restore_definitions(self):
            pass

    service = Service()

    def health(release, home, started, prior_pids):
        if release == new:
            if failure == "state-write":
                (home / "state.db").write_bytes(b"new-message")
                return None
            if failure == "startup" or (failure == "restart" and service.opens >= 2):
                return None
        return {service.opens + 100}

    kwargs = dict(
        pointers=pointers,
        service=service,
        release=new,
        profile=candidate,
        expected=pointers.read_pair(),
        preflight=lambda: None,
        health=health,
        timeout=0.05,
    )
    if failure == "state-write":
        with pytest.raises(LifecycleBlockedError, match="reconciliation"):
            run_promotion(**kwargs)
        assert pointers.read_pair()[0] == str(new)
        assert (candidate / "state.db").read_bytes() == b"new-message"
        assert not service.running
    else:
        result = run_promotion(**kwargs)
        assert service.running
        if failure == "none":
            assert result["status"] == "PROMOTED_CLEAR"
            assert pointers.read_pair() == (str(new), str(candidate), 2)
            assert service.opens == 2
        else:
            assert result["status"] == "ROLLED_BACK_PRE_TRAFFIC"
            assert pointers.read_pair() == (str(old), str(profile), 1)
        assert (profile / "state.db").read_bytes() == b"original"


def test_preflight_failure_never_stops_services(tmp_path):
    assert importlib.util.find_spec("ik_lifecycle.guarded_promotion")
    from ik_lifecycle.guarded_promotion import run_promotion

    class Untouchable:
        def close(self):
            pytest.fail("service stopped before all artifact gates passed")

    def blocked():
        raise LifecycleBlockedError("bad-artifact", "bad artifact")

    with pytest.raises(LifecycleBlockedError, match="bad artifact"):
        run_promotion(
            pointers=None,
            service=Untouchable(),
            release=tmp_path,
            profile=tmp_path,
            expected=(),
            preflight=blocked,
            health=None,
            timeout=0.01,
        )
