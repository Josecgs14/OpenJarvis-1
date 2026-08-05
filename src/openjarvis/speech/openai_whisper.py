"""OpenAI Whisper API speech-to-text backend (cloud)."""

from __future__ import annotations

import io
import os
from typing import List, Optional

import httpx

from openjarvis.core.registry import SpeechRegistry
from openjarvis.speech._stubs import SpeechBackend, TranscriptionResult

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore[assignment, misc]


@SpeechRegistry.register("openai")
class OpenAIWhisperBackend(SpeechBackend):
    """Cloud speech-to-text using OpenAI Whisper API."""

    backend_id = "openai"

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._client: Optional[OpenAI] = None
        if self._api_key and OpenAI is not None:
            self._client = OpenAI(api_key=self._api_key)

    def transcribe(
        self,
        audio: bytes,
        *,
        format: str = "wav",
        language: Optional[str] = None,
    ) -> TranscriptionResult:
        """Transcribe audio using OpenAI's Whisper API."""
        if self._client is None:
            if self._api_key:
                return self._transcribe_http(audio, format=format, language=language)
            raise RuntimeError("OpenAI client not initialized (missing API key?)")

        ext = format if not format.startswith(".") else format[1:]
        audio_file = io.BytesIO(audio)
        audio_file.name = f"audio.{ext}"

        kwargs: dict = {"model": "whisper-1", "file": audio_file}
        if language:
            kwargs["language"] = language
        kwargs["response_format"] = "verbose_json"

        response = self._client.audio.transcriptions.create(**kwargs)

        return TranscriptionResult(
            text=getattr(response, "text", str(response)),
            language=getattr(response, "language", None),
            confidence=None,
            duration_seconds=getattr(response, "duration", 0.0),
            segments=[],
        )

    def _transcribe_http(
        self,
        audio: bytes,
        *,
        format: str,
        language: Optional[str],
    ) -> TranscriptionResult:
        """Raw-HTTP fallback for ``/v1/audio/transcriptions``.

        Used when the ``openai`` package can't be imported — e.g. on
        Termux, where its Rust-based deps ``jiter``/``pydantic-core`` have
        no prebuilt wheels and fail to build from source.
        """
        ext = format if not format.startswith(".") else format[1:]
        base_url = os.environ.get(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        ).rstrip("/")
        data = {"model": "whisper-1", "response_format": "verbose_json"}
        if language:
            data["language"] = language
        files = {"file": (f"audio.{ext}", audio)}
        headers = {"Authorization": f"Bearer {self._api_key}"}

        resp = httpx.post(
            f"{base_url}/audio/transcriptions",
            data=data,
            files=files,
            headers=headers,
            timeout=120.0,
        )
        resp.raise_for_status()
        result = resp.json()

        return TranscriptionResult(
            text=result.get("text", ""),
            language=result.get("language"),
            confidence=None,
            duration_seconds=result.get("duration", 0.0),
            segments=[],
        )

    def health(self) -> bool:
        return bool(self._api_key)

    def supported_formats(self) -> List[str]:
        return ["mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm"]
