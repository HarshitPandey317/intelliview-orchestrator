# import pytest
# import secrets
# from datetime import datetime, timedelta, timezone
# from unittest.mock import patch, MagicMock

# # First patch standard network dependencies to avoid issues during startup
# with (
#     patch("redis.from_url", return_value=MagicMock()),
#     patch("sqlalchemy.create_engine", return_value=MagicMock()),
# ):
#     from orchestrator.main import app

# from fastapi.testclient import TestClient
# from database.models import Candidate

# client = TestClient(app)

# @pytest.fixture
# def mock_smtp(mocker):
#     """Mock the SMTP send_message call to avoid real network requests."""
#     return mocker.patch("smtplib.SMTP", autospec=True)
import secrets
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Patch only external network connections like redis during startup
with patch("redis.from_url", return_value=MagicMock()):
    from orchestrator.main import app

from database.db import get_db
from database.models import Candidate

client = TestClient(app)


@pytest.fixture(autouse=True)
def override_get_db(db_session, mocker):
    """Use the same PostgreSQL session for FastAPI and manager-created sessions."""
    app.dependency_overrides[get_db] = lambda: db_session
    mocker.patch("orchestrator.candidate_manager.SessionLocal", return_value=db_session)
    mocker.patch("orchestrator.session_manager.SessionLocal", return_value=db_session)
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def mock_smtp(mocker):
    """Mock the SMTP send_message call to avoid real network requests."""
    return mocker.patch("smtplib.SMTP", autospec=True)


@pytest.fixture
def mock_smtp_ssl(mocker):
    """Mock the SMTP_SSL send_message call to avoid real network requests."""
    return mocker.patch("smtplib.SMTP_SSL", autospec=True)


def test_registration_generates_token_and_sends_email(db_session, mock_smtp):
    """
    Test that registering a new candidate generates a token, stores it in the Candidate DB,
    and attempts to send a verification email.
    """
    email = f"candidate-{secrets.token_hex(4)}@example.com"
    payload = {"name": "New Candidate", "email": email, "skills": ["python", "fastapi"]}

    # Call candidates creation API
    response = client.post("/candidates", json=payload)
    assert response.status_code == 200

    data = response.json()
    candidate_id = data["candidate_id"]

    # Verify candidate was created in the DB with a token and is unverified
    cand_db = db_session.query(Candidate).filter_by(candidate_id=candidate_id).first()
    assert cand_db is not None
    assert cand_db.email_verified is False
    assert cand_db.verification_token is not None
    assert cand_db.verification_token_expires_at is not None

    # Verify SMTP mock was triggered to send the email
    assert mock_smtp.called or mock_smtp.return_value.__enter__.called


def test_valid_token_verifies_candidate(db_session):
    """
    Test that calling the verify-email endpoint with a valid token
    marks the candidate as verified and invalidates the token.
    """
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

    cand = Candidate(
        candidate_id="cand-verify-success",
        name="Successful Verifier",
        email="success@example.com",
        email_verified=False,
        verification_token=token,
        verification_token_expires_at=expires_at,
    )
    db_session.add(cand)
    db_session.commit()

    # Call verification endpoint
    response = client.get(f"/verify-email?token={token}")
    assert response.status_code == 200
    assert response.json()["message"] == "Email verified successfully"

    # Verify Candidate DB state
    db_session.expire_all()
    cand_db = (
        db_session.query(Candidate)
        .filter_by(candidate_id="cand-verify-success")
        .first()
    )
    assert cand_db.email_verified is True
    assert cand_db.verification_token is None
    assert cand_db.verification_token_expires_at is None


def test_invalid_token_rejected(db_session):
    """
    Test that verifying with a random or non-existent token is rejected.
    """
    response = client.get("/verify-email?token=nonexistent-token-12345")
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid token"


def test_expired_token_rejected(db_session):
    """
    Test that verifying with an expired token is rejected and candidate remains unverified.
    """
    token = secrets.token_urlsafe(32)
    # Expiry is 1 hour in the past
    expires_at = datetime.now(timezone.utc) - timedelta(hours=1)

    cand = Candidate(
        candidate_id="cand-verify-expired",
        name="Expired Verifier",
        email="expired@example.com",
        email_verified=False,
        verification_token=token,
        verification_token_expires_at=expires_at,
    )
    db_session.add(cand)
    db_session.commit()

    # Call verification endpoint
    response = client.get(f"/verify-email?token={token}")
    assert response.status_code == 400
    assert response.json()["detail"] == "Verification token has expired"

    # Candidate should still be unverified in DB
    db_session.expire_all()
    cand_db = (
        db_session.query(Candidate)
        .filter_by(candidate_id="cand-verify-expired")
        .first()
    )
    assert cand_db.email_verified is False


@patch("orchestrator.main.scheduler.can_accept_task", return_value=True)
@patch("orchestrator.main.scheduler.schedule_task", return_value=None)
@patch("orchestrator.main.scheduler.get_estimated_wait_time", return_value=5)
def test_unverified_booking_rejected(
    mock_est_wait, mock_schedule, mock_capacity, db_session
):
    """
    Test that booking an interview for an unverified candidate is rejected.
    """
    cand = Candidate(
        candidate_id="cand-unverified-booker",
        name="Unverified Booker",
        email="unverified@example.com",
        email_verified=False,
    )
    db_session.add(cand)
    db_session.commit()

    # Attempt to start interview
    response = client.post(
        "/start-interview",
        headers={"X-API-Token": "ci-test-token"},
        json={"candidate_id": "cand-unverified-booker", "priority": "medium"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Candidate email not verified"


@patch("orchestrator.main.scheduler.can_accept_task", return_value=True)
@patch("orchestrator.main.scheduler.schedule_task", return_value=None)
@patch("orchestrator.main.scheduler.get_estimated_wait_time", return_value=5)
def test_verified_booking_allowed(
    mock_est_wait, mock_schedule, mock_capacity, db_session
):
    """
    Test that booking an interview for a verified candidate is allowed.
    """
    cand = Candidate(
        candidate_id="cand-verified-booker",
        name="Verified Booker",
        email="verified@example.com",
        email_verified=True,
    )
    db_session.add(cand)
    db_session.commit()

    # Attempt to start interview
    response = client.post(
        "/start-interview",
        headers={"X-API-Token": "ci-test-token"},
        json={"candidate_id": "cand-verified-booker", "priority": "medium"},
    )
    assert response.status_code == 200
    assert response.json()["session_id"].startswith("session_")
