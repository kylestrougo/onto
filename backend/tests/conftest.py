import os
import tempfile

import pytest

os.environ.setdefault("ONTO_COOKIE_SECURE", "0")

from onto import create_app  # noqa: E402
from onto.config import Config  # noqa: E402


@pytest.fixture()
def app():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    upload_dir = tempfile.mkdtemp(prefix="onto-uploads-")

    class TestConfig(Config):
        DATABASE = path
        UPLOAD_DIR = upload_dir
        SECRET_KEY = "test-secret"
        TESTING = True
        SESSION_COOKIE_SECURE = False
        OPENROUTER_API_KEY = "test-key"
        ADMIN_USERNAME = "admin"
        SIGNUP_CAP_IP = 3
        DAILY_CAP_USER = 10
        MAIL_DRY_RUN = True
        DEFAULT_TZ = "America/New_York"

    application = create_app(TestConfig)
    yield application

    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except OSError:
            pass
    import shutil

    shutil.rmtree(upload_dir, ignore_errors=True)


@pytest.fixture()
def client(app):
    return app.test_client()


def sign_up(client, username="kyle", password="longenoughpw", timezone="America/New_York"):
    return client.post(
        "/signup",
        data={"username": username, "password": password, "timezone": timezone},
    )


@pytest.fixture()
def signed_in(client):
    sign_up(client)
    return client


@pytest.fixture()
def admin(client):
    sign_up(client, username="admin")
    return client
