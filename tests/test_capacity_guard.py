from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from orchestrator.main import app, get_current_user, get_db


@pytest.fixture
def client_override():
    # 1. Mock DB Session & Candidate
    mock_candidate = MagicMock()
    mock_candidate.candidate_id = "test123"
    mock_candidate.email_verified = True

    mock_db_session = MagicMock()
    mock_db_session.execute.return_value.scalar_one_or_none.return_value = mock_candidate

    # 2. Override FastAPI Dependencies
    app.dependency_overrides[get_db] = lambda: mock_db_session
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test_user"}

    client = TestClient(app)
    yield client

    # Clear overrides after test cleanup
    app.dependency_overrides.clear()


def test_returns_503_when_no_workers_available(client_override):
    with (
        patch("orchestrator.main.session_manager") as mock_session_manager,
        patch("orchestrator.main.scheduler") as mock_scheduler,
    ):
        mock_session_manager.create_session.return_value = "session_test123"
        mock_scheduler.can_accept_task.return_value = False

        response = client_override.post(
            "/start-interview",
            json={
                "candidate_id": "test123",
                "candidate_name": "John Doe",
                "position": "Developer",
                "priority": "medium",
            },
        )

    assert response.status_code == 503
    assert response.headers.get("Retry-After") == "5"
    body = response.json()
    assert body["error"] == "service_unavailable"


def test_capacity_check_exception_fails_safe_to_503(client_override):
    with (
        patch("orchestrator.main.session_manager") as mock_session_manager,
        patch("orchestrator.main.scheduler") as mock_scheduler,
    ):
        mock_session_manager.create_session.return_value = "session_test123"
        mock_scheduler.can_accept_task.side_effect = RuntimeError("redis down")

        response = client_override.post(
            "/start-interview",
            json={
                "candidate_id": "test123",
                "candidate_name": "John Doe",
                "position": "Developer",
                "priority": "medium",
            },
        )
    assert response.status_code == 503
