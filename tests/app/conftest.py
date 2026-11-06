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
