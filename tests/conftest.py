import pytest
import mongomock
from unittest.mock import patch, MagicMock, PropertyMock
from app import create_app


def _make_mock_mongo():
    """
    Build a mock PyMongo object backed by a real mongomock in-memory client.
    mongo.db returns the mongomock database, so create_index() calls work.
    """
    mock_client = mongomock.MongoClient()
    mock_db     = mock_client["test_road_nextgen"]

    m = MagicMock()
    m.db = mock_db           # <- real mongomock DB, not None
    m.init_app = MagicMock() # <- silences the actual PyMongo init
    return m


@pytest.fixture(scope="session")
def app():
    """
    Create a Flask test app wired to mongomock (in-memory MongoDB).
    No real DB or external service is needed.
    """
    mock_mongo  = _make_mock_mongo()
    mock_sio    = MagicMock()
    mock_oauth  = MagicMock()

    with patch("extensions.mongo",    mock_mongo), \
         patch("extensions.socketio", mock_sio), \
         patch("extensions.oauth",    mock_oauth), \
         patch("flask_pymongo.PyMongo.init_app", MagicMock()), \
         patch("cloudinary.config",   MagicMock()):

        flask_app = create_app()

        flask_app.config.update({
            "TESTING":              True,
            "SECRET_KEY":           "ci-test-secret-key",
            "MONGO_URI":            "mongodb://localhost:27017/test_road_nextgen",
            "CLOUDINARY_CLOUD_NAME":"dummy",
            "CLOUDINARY_API_KEY":   "dummy",
            "CLOUDINARY_API_SECRET":"dummy",
            "GOOGLE_CLIENT_ID":     "dummy",
            "GOOGLE_CLIENT_SECRET": "dummy",
            "MAILER_SERVICE_URL":   "http://dummy-mailer",
            "MAILER_API_SECRET":    "dummy",
            "YOLO_MODEL_PATH":      None,
            "SESSION_COOKIE_SECURE":False,
            "WTF_CSRF_ENABLED":     False,
        })

        flask_app.mongo  = mock_mongo   
        flask_app.db     = mock_mongo.db

        yield flask_app


@pytest.fixture
def client(app):
    """Flask test client for making HTTP requests."""
    return app.test_client()


@pytest.fixture
def mock_mongo_db():
    """Standalone in-memory MongoDB for direct model tests."""
    client = mongomock.MongoClient()
    db = client["test_db"]
    yield db
    client.close()