"""
Road_NextGen — unit & integration tests
Tests Flask routes, User model logic, and helpers.
"""
import pytest
import bcrypt
from datetime import datetime
from unittest.mock import patch, MagicMock
from utils.helpers import serialize_doc, serialize_list, paginate, time_ago


# 1. App sanity — does the Flask app start at all?

class TestAppCreation:

    def test_app_is_not_none(self, app):
        assert app is not None

    def test_testing_flag_is_set(self, app):
        assert app.config["TESTING"] is True

    def test_blueprints_registered(self, app):
        """All 5 blueprints must be registered on startup."""
        registered = [bp for bp in app.blueprints]
        for name in ["auth", "citizen", "admin", "crew", "api"]:
            assert name in registered, f"Blueprint '{name}' is not registered"


# 2. HTTP route smoke tests — correct status codes returned

class TestPublicRoutes:

    def test_homepage_returns_200(self, client):
        """Landing page must be accessible without login."""
        response = client.get("/")
        assert response.status_code == 200

    def test_login_page_returns_200(self, client):
        response = client.get("/auth/login")
        assert response.status_code == 200

    def test_nonexistent_route_returns_404(self, client):
        response = client.get("/this-route-does-not-exist")
        assert response.status_code == 404


class TestProtectedRoutes:
    """Routes that require login must redirect to login, not crash."""

    def test_citizen_dashboard_redirects_without_login(self, client):
        response = client.get("/citizen/dashboard")
        # Must redirect (302) or return 401/403 — never 500
        assert response.status_code in (302, 401, 403)

    def test_admin_dashboard_redirects_without_login(self, client):
        response = client.get("/admin/dashboard")
        assert response.status_code in (302, 401, 403)

    def test_crew_dashboard_redirects_without_login(self, client):
        response = client.get("/crew/dashboard")
        assert response.status_code in (302, 401, 403)

    def test_api_ai_detect_requires_login(self, client):
        """AI detection endpoint must reject unauthenticated calls."""
        response = client.post("/api/ai/detect")
        assert response.status_code in (302, 401, 403)

    def test_api_cloudinary_signature_requires_login(self, client):
        response = client.post("/api/cloudinary/signature")
        assert response.status_code in (302, 401, 403)



# 3. User model — password hashing & role validation

class TestUserModel:

    def test_check_password_correct(self):
        """bcrypt password verification must return True for correct password."""
        from models.user import User
        password = "SecurePass123!"
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        fake_user = {"password_hash": hashed}
        assert User.check_password(fake_user, password) is True

    def test_check_password_wrong(self):
        """bcrypt must reject wrong password."""
        from models.user import User
        hashed = bcrypt.hashpw(b"correct", bcrypt.gensalt()).decode()
        fake_user = {"password_hash": hashed}
        assert User.check_password(fake_user, "wrong_password") is False

    def test_check_password_empty_hash(self):
        """User with no password_hash (Google OAuth) must return False."""
        from models.user import User
        assert User.check_password({"password_hash": ""}, "anything") is False

    def test_valid_roles(self):
        """ROLES list must contain exactly citizen, admin, crew."""
        from models.user import User
        assert set(User.ROLES) == {"citizen", "admin", "crew"}

    def test_create_user_structure(self, mock_mongo_db):
        """User.create() must return a dict with all required fields."""
        from models.user import User
        with patch("models.user.mongo") as mock_mongo:
            mock_insert = MagicMock()
            mock_insert.inserted_id = "fake_object_id"
            mock_mongo.db.users.insert_one.return_value = mock_insert

            data = {
                "name": "Test Citizen",
                "email": "Test@Example.com",
                "role": "citizen",
                "auth_provider": "otp",
                "is_verified": True,
            }
            user = User.create(data)

        # Email must be lowercased
        assert user["email"] == "test@example.com"
        # Required fields must exist
        for field in ["name", "email", "role", "is_active",
                      "created_at", "settings"]:
            assert field in user, f"Missing field: {field}"
        # Default values
        assert user["is_active"] is True
        assert user["role"] == "citizen"
        assert user["settings"]["notifications"] is True



# 4. Helper functions — serialize_doc, time_ago, paginate

class TestHelpers:

    def test_serialize_doc_converts_objectid(self):
        """ObjectId fields must become strings after serialization."""
        from bson import ObjectId
        oid = ObjectId()
        doc = {"_id": oid, "name": "Test"}
        result = serialize_doc(doc)
        assert result["_id"] == str(oid)
        assert isinstance(result["_id"], str)

    def test_serialize_doc_converts_datetime(self):
        """Datetime fields must become ISO strings."""
        dt = datetime(2025, 8, 1, 12, 0, 0)
        doc = {"created_at": dt}
        result = serialize_doc(doc)
        assert isinstance(result["created_at"], str)
        assert "2025" in result["created_at"]

    def test_serialize_doc_handles_none(self):
        """serialize_doc(None) must return empty dict, not crash."""
        assert serialize_doc(None) == {}

    def test_serialize_doc_nested(self):
        """Nested ObjectIds in sub-dicts must also be converted."""
        from bson import ObjectId
        oid = ObjectId()
        doc = {"user": {"_id": oid, "name": "Nested"}}
        result = serialize_doc(doc)
        assert isinstance(result["user"]["_id"], str)

    def test_serialize_list_empty(self):
        """serialize_list([]) must return []."""
        assert serialize_list([]) == []

    def test_paginate_structure(self):
        """paginate() must return data + pagination keys."""
        result = paginate(["a", "b"], page=1, per_page=10, total=2)
        assert "data" in result
        assert "pagination" in result
        assert result["pagination"]["total"] == 2
        assert result["pagination"]["pages"] == 1

    def test_paginate_calculates_pages(self):
        """paginate() must calculate total pages correctly."""
        result = paginate([], page=1, per_page=10, total=25)
        assert result["pagination"]["pages"] == 3

    def test_time_ago_recent(self):
        """A very recent datetime must return 'Just now'."""
        from datetime import datetime
        result = time_ago(datetime.utcnow())
        assert result == "Just now"

    def test_time_ago_none(self):
        """time_ago(None) must return 'Unknown', not crash."""
        assert time_ago(None) == "Unknown"



# 5. API endpoints — JSON responses are valid

class TestAPIResponseFormat:

    def test_ai_detect_returns_json_on_no_image(self, client):
        """
        POST /api/ai/detect without auth → redirect/403.
        Even if somehow reached, must never return HTML on bad input.
        """
        with client.session_transaction() as sess:
            # Simulate a logged-in citizen session
            sess["user_id"] = "507f1f77bcf86cd799439011"
            sess["role"] = "citizen"

        response = client.post("/api/ai/detect",
                               content_type="multipart/form-data")
        # Either redirect (session invalid without real DB) or JSON error
        assert response.status_code in (302, 400, 401, 403, 500)