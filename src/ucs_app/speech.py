"""Bounded, review-only transcription. No workflow or agent dependencies."""

import asyncio
import json
import os
import signal
import struct
import sys
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import AsyncIterator, Callable, Optional, Protocol

from fastapi import FastAPI, HTTPException, Request, Response
from starlette.requests import ClientDisconnect

MAX_SECONDS = 30
MAX_BYTES = 44 + 48000 * 2 * MAX_SECONDS
GOOGLE_ENDPOINT = "https://www.google.com/speech-api/v2/recognize"
ERRORS = {
    "silence": (422, "No speech was detected. Check your microphone and try again."),
    "unintelligible": (422, "Speech was not understood. Speak clearly or type your request."),
    "network": (502, "Google transcription is unavailable. Check your connection or type your request."),
    "timeout": (504, "Transcription timed out. Try a shorter recording or type your request."),
    "unavailable": (503, "Speech transcription is unavailable. You can still type your request."),
}


class SpeechFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code if code in ERRORS else "unavailable"
        super().__init__(self.code)


class Transcriber(Protocol):
    async def transcribe(self, wav: bytes) -> str:
        """Return only editable text; never submit it."""
        ...


def validate_wav(data: bytes) -> None:
    """Accept the recorder's canonical mono PCM16 WAV, not MIME claims."""
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Recording is too large. Record at most 30 seconds.")
    valid = len(data) >= 44
    if valid:
        riff, size, wave, fmt, fmt_size, encoding, channels, rate, byte_rate, align, bits, tag, length = struct.unpack(
            "<4sI4s4sIHHIIHH4sI", data[:44]
        )
        valid = (
            riff == b"RIFF" and wave == b"WAVE" and fmt == b"fmt " and fmt_size == 16
            and encoding == 1 and channels == 1 and 8000 <= rate <= 48000
            and bits == 16 and align == 2 and byte_rate == rate * 2 and tag == b"data"
            and size == len(data) - 8 and length == len(data) - 44 and length % 2 == 0
        )
        if valid and not 0.1 <= length / (rate * 2) <= MAX_SECONDS:
            raise HTTPException(422, "Record between 0.1 and 30 seconds of speech.")
    if not valid:
        raise HTTPException(415, "Unsupported audio. Use the microphone controls to record mono PCM16 WAV.")
    if not any(data[44:]):
        raise SpeechFailure("silence")


class GoogleTranscriber:
    """A disposable process bounds encoding, DNS, network reads and parsing together."""

    def __init__(self, *, language: str = "en-AU", timeout: float = 15) -> None:
        self.language = language
        self.timeout = timeout

    async def transcribe(self, wav: bytes) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "ucs_app.speech_worker", self.language,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
            )
        except OSError:
            raise SpeechFailure("unavailable") from None
        try:
            try:
                output, _ = await asyncio.wait_for(process.communicate(wav), self.timeout)
            except asyncio.TimeoutError:
                raise SpeechFailure("timeout") from None
            if process.returncode != 0:
                raise SpeechFailure("unavailable")
            try:
                result = json.loads(output)
            except (ValueError, UnicodeError):
                raise SpeechFailure("unavailable") from None
            if not isinstance(result, dict):
                raise SpeechFailure("unavailable")
            if "error" in result:
                raise SpeechFailure(str(result["error"]))
            transcript = result.get("transcript")
            if not isinstance(transcript, str) or not 1 <= len(transcript.strip()) <= 4000:
                raise SpeechFailure("unintelligible")
            return transcript.strip()
        finally:
            # Includes the FLAC encoder child. Timeout/cancellation cannot leave a
            # worker occupying resources after its concurrency slot is released.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()


def add_speech_routes(
    app: FastAPI, require_session: Callable[[Request], object],
    transcriber: Optional[Transcriber] = None,
) -> None:
    adapter = transcriber if transcriber is not None else GoogleTranscriber(
        language=os.getenv("UCS_SPEECH_LANGUAGE", "en-AU")
    )
    slots: Optional[asyncio.Semaphore] = None

    @asynccontextmanager
    async def admission() -> AsyncIterator[None]:
        nonlocal slots
        # Python 3.9 binds semaphores at creation: construct on the serving loop,
        # not in the synchronous application factory.
        if slots is None:
            slots = asyncio.Semaphore(2)
        if slots.locked():
            raise HTTPException(429, "Transcription is busy. Please try again shortly.")
        await slots.acquire()
        try:
            yield
        finally:
            slots.release()

    @app.get("/speech/{asset}")
    async def speech_asset(asset: str) -> Response:
        if asset not in {"speech.js", "recorder.js"}:
            raise HTTPException(404)
        return Response(files("ucs_app").joinpath("web").joinpath(asset).read_text(), media_type="text/javascript")

    @app.post("/speech/transcriptions")
    async def transcribe(request: Request) -> Response:
        require_session(request)
        if request.headers.get("content-type", "").split(";")[0] != "audio/wav":
            raise HTTPException(415, "Upload PCM WAV audio using the microphone controls.")
        async with admission():
            async def receive() -> bytes:
                audio = bytearray()
                async for chunk in request.stream():
                    if len(audio) + len(chunk) > MAX_BYTES:
                        raise HTTPException(413, "Recording is too large. Record at most 30 seconds.")
                    audio.extend(chunk)
                return bytes(audio)

            try:
                try:
                    wav = await asyncio.wait_for(receive(), timeout=10)
                except asyncio.TimeoutError:
                    raise SpeechFailure("timeout") from None
                except ClientDisconnect:
                    raise HTTPException(400, "Audio upload was interrupted. Please try again.") from None
                validate_wav(wav)
                transcript = await adapter.transcribe(wav)
                if not transcript.strip() or len(transcript) > 4000:
                    raise SpeechFailure("unintelligible")
            except SpeechFailure as error:
                code, message = ERRORS[error.code]
                raise HTTPException(code, message) from None
            return Response(json.dumps({"transcript": transcript}), media_type="application/json",
                            headers={"Cache-Control": "no-store"})
