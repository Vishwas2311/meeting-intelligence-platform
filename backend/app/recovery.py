"""Azure Fast Transcription is an optional, not-yet-wired recovery adapter.

Actual recovery needs audio format conversion, gap detection, timestamp reconciliation,
endpoint version verification, and authenticated retry jobs.
"""
from .providers import fast_transcribe_file
