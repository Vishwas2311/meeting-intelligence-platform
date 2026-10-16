from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config=SettingsConfigDict(env_file='.env', extra='ignore')
    app_name:str='Meeting Intelligence API'
    database_url:str='sqlite:///./meeting_intelligence.db'
    storage_dir:Path=Path('./data')
    cors_origins:str='http://localhost:5173'
    app_api_token:str=''
    azure_speech_region:str=''
    azure_speech_endpoint:str=''
    azure_speech_key:str=''
    azure_speech_key_secondary:str=''
    azure_speech_fast_endpoint:str=''
    azure_speech_fast_api_version:str=''
    llm_endpoint:str=''
    llm_api_key:str=''
    llm_model:str=''
    llm_api_version:str=''
    llm_max_input_chars:int=100000
    internal_form_api_url:str=''
    internal_form_api_token:str=''
    internal_form_submission_enabled:bool=False
    audio_retention_days:int=30
    max_audio_chunk_mb:int=16

@lru_cache
def get_settings():return Settings()
