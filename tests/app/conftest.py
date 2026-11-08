import pytest

from toystudio.app.i18n import build_translations, install_language


@pytest.fixture(scope="session", autouse=True)
def _translations():
    return build_translations()


@pytest.fixture(autouse=True)
def _reset_app_state(qapp):
    yield
    qapp.setStyleSheet("")
    install_language(qapp, "ru")


@pytest.fixture
def make_shell(qtbot, tmp_path):
    """Shell on a throw-away app.db and projects folder; closed stores after the test."""
    from toystudio.app.shell import Shell
    from toystudio.storage.app_store import AppStore

    stores = []

    def make(**kw):
        store = AppStore(tmp_path / "home" / "app.db")
        stores.append(store)
        w = Shell(store, tmp_path / "home" / "projects", **kw)
        w._ask_close = lambda: "stop"  # pytest-qt closes widgets at teardown: never block
        qtbot.addWidget(w)
        return w

    yield make
    for s in stores:
        s.close()
