import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.mark.parametrize(
    "field", ["llm_max_input_chars", "audio_retention_days", "max_audio_chunk_mb"]
)
@pytest.mark.parametrize("value", [0, -1])
def test_limits_must_be_positive(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_windows_utf8_environment_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text("APP_NAME=Test meeting service\n", encoding="utf-8-sig")
    assert Settings(_env_file=path).app_name == "Test meeting service"
