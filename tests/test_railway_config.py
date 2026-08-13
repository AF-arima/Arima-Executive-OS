from pathlib import Path
import tomllib


def test_predeploy_migrates_then_bootstraps_agent_platform() -> None:
    config = tomllib.loads(Path("railway.toml").read_text())

    assert config["deploy"]["preDeployCommand"] == [
        "python -m alembic upgrade head "
        "&& python -m app.services.agent_bootstrap"
    ]
