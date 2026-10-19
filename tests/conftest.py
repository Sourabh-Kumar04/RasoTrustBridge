import sys
import os

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app
os.environ["ENVIRONMENT"] = "testing"
from app.config import get_settings

settings = get_settings()

# Use in-memory SQLite database for isolated fast test execution
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

test_engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="function")
def db_session():
    """Create a fresh database session for each test function."""
    import app.models  # ensure models are registered
    Base.metadata.create_all(bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(scope="function")
def client(db_session):
    """FastAPI TestClient with overridden get_db and default admin auth for existing test suite compatibility."""
    from app.models.user import User
    from app.services.auth_service import hash_password, create_access_token

    # Seed default system user so existing 30 tests continue passing seamlessly
    system_user = User(
        id="usr_system_default",
        email="system@trustbridge.network",
        hashed_password=hash_password("DevSecretPassword123!"),
        role="admin",
        display_name="TrustBridge System",
    )
    db_session.add(system_user)
    db_session.commit()

    token = create_access_token({
        "sub": system_user.id,
        "email": system_user.email,
        "role": system_user.role,
        "display_name": system_user.display_name,
    })

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        test_client.headers.update({"Authorization": f"Bearer {token}"})
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def unauth_client(db_session):
    """FastAPI TestClient with overridden get_db but NO Authorization header."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
