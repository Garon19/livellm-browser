from pathlib import Path


DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


def test_dockerfile_is_compatible_with_browser_operator_uid_1000():
    """The Browser operator runs browser pods as uid/gid 1000."""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "ENV HEADLESS_USER_ID=1000 HEADLESS_USER_GROUP_ID=1000" in dockerfile
    assert ": > /dockerstartup/.initial_sudo_password" in dockerfile
    assert 'USER "${HEADLESS_USER_ID}"' in dockerfile
