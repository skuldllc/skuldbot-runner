import pytest

from skuldbot_runner.api_client import OrchestratorClient
from skuldbot_runner.artifact_uploader import ArtifactUploadError, OrchestratorArtifactUploader
from skuldbot_runner.config import RunnerConfig
from skuldbot_runner.orchestrator_url import require_orchestrator_api_v1_url


def test_orchestrator_url_must_be_versioned_api_base():
    assert (
        require_orchestrator_api_v1_url("https://apidataplane.skuldbot.com/api/v1/")
        == "https://apidataplane.skuldbot.com/api/v1"
    )

    with pytest.raises(ValueError, match="/api/v1"):
        require_orchestrator_api_v1_url("https://apidataplane.skuldbot.com/api")

    with pytest.raises(ValueError, match="/api/v1"):
        require_orchestrator_api_v1_url("https://apidataplane.skuldbot.com")


def test_runner_client_fails_closed_on_unversioned_orchestrator_url():
    config = RunnerConfig(orchestrator_url="https://apidataplane.skuldbot.com/api")

    with pytest.raises(ValueError, match="/api/v1"):
        OrchestratorClient(config)


def test_artifact_uploader_rejects_unversioned_orchestrator_url(tmp_path):
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("evidence")
    uploader = OrchestratorArtifactUploader(
        {
            "SKULDBOT_ORCHESTRATOR_URL": "https://apidataplane.skuldbot.com/api",
            "SKULDBOT_API_KEY": "skr_test",
            "SKULDBOT_EVIDENCE_CLASSIFICATION": "internal",
        }
    )

    with pytest.raises(ArtifactUploadError, match="/api/v1"):
        uploader.upload(
            run_id="run-1",
            action="screenshot",
            artifact_path=artifact,
            checksum_sha256="0" * 64,
            mime_type="text/plain",
        )
