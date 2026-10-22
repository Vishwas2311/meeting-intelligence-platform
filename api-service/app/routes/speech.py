"""Speech API endpoints."""

from fastapi import APIRouter, Depends, Request

from .. import response_models as contracts
from ..config import Settings, get_settings
from ..languages import AUTO_LANGUAGES
from ..security import require_actor

router = APIRouter()


@router.post(
    "/api/speech/token", response_model=contracts.SpeechTokenView, response_model_exclude_unset=True
)
async def token(
    request: Request,
    actor: str = Depends(require_actor),
    settings: Settings = Depends(get_settings),
):
    credentials = await request.app.state.speech_tokens.get(settings)
    return {**credentials, "auto_detect_languages": list(AUTO_LANGUAGES)}


@router.get(
    "/api/speech/languages",
    response_model=contracts.LanguagesView,
    response_model_exclude_unset=True,
)
def speech_languages(actor: str = Depends(require_actor)):
    return {"auto_detect_languages": list(AUTO_LANGUAGES)}
