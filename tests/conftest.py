import pytest
import mongomock
from unittest.mock import patch, MagicMock
from app import create_app


@pytest.fixture(scope="session")
def app():
    """
    Create a Flask test app wired to mongomock (in-memory MongoDB).
    No real DB or external service is needed.
    """
    test_config = {
        "TESTING": True,
        "SECRET_KEY": "ci-test-secret-key",
        "MONGO_URI": "mongodb://localhost:27017/test_road_nextgen",
        "CLOUDINARY_CLOUD_NAME": "dummy",
        "CLOUDINARY_API_KEY": "dummy",
        "CLOUDINARY_API_SECRET": "dummy",
        "GOOGLE_CLIENT_ID": "dummy",
        "GOOGLE_CLIENT_SECRET": "dummy",
        "MAILER_SERVICE_URL": "http://dummy-mailer",
        "MAILER_API_SECRET": "dummy",
        "YOLO_MODEL_PATH": None,
        "SESSION_COOKIE_SECURE": False,
        "WTF_CSRF_ENABLED": False,
    }

    # Patch mongomock so no real MongoDB is needed
    with patch("flask_pymongo.PyMongo.init_app"), \
         patch("extensions.mongo") as mock_mongo, \
         patch("extensions.socketio") as mock_sio, \
         patch("extensions.oauth") as mock_oauth:

        # Wire up an in-memory mongomock client
        mock_client = mongomock.MongoClient()
        mock_mongo.db = mock_client["test_road_nextgen"]

        flask_app = create_app()
        flask_app.config.update(test_config)

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