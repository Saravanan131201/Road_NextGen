import pytest
import mongomock
from unittest.mock import patch, MagicMock


def _make_mock_mongo():
    
    mock_client = mongomock.MongoClient()
    mock_db     = mock_client["test_road_nextgen"]

    m = MagicMock()
    m.db        = mock_db    
    m.init_app  = MagicMock() 
    return m


@pytest.fixture(scope="session")
def app():
    """
    Flask test app wired to mongomock in-memory database.
    Patches are applied to the names as app.py sees them at runtime.
    """
    mock_mongo = _make_mock_mongo()
    mock_sio   = MagicMock()
    mock_oauth = MagicMock()

    with \
        patch("app.mongo",    mock_mongo), \
        patch("app.socketio", mock_sio),   \
        patch("app.oauth",    mock_oauth):

        from app import create_app
        flask_app = create_app()

        flask_app.config.update({
            "TESTING":               True,
            "SECRET_KEY":            "ci-test-secret-key",
            "MONGO_URI":             "mongodb://localhost:27017/test_road_nextgen",
            "CLOUDINARY_CLOUD_NAME": "dummy",
            "CLOUDINARY_API_KEY":    "dummy",
            "CLOUDINARY_API_SECRET": "dummy",
            "GOOGLE_CLIENT_ID":      "dummy",
            "GOOGLE_CLIENT_SECRET":  "dummy",
            "MAILER_SERVICE_URL":    "http://dummy-mailer",
            "MAILER_API_SECRET":     "dummy",
            "YOLO_MODEL_PATH":       None,
            "SESSION_COOKIE_SECURE": False,
            "WTF_CSRF_ENABLED":      False,
        })

        yield flask_app


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture
def mock_mongo_db():
    """Standalone in-memory MongoDB for direct model tests."""
    client = mongomock.MongoClient()
    db = client["test_db"]
    yield db
    client.close()