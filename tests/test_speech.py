"""Deterministic speech validation, transport, isolation and deadline checks."""

import asyncio
import io
import struct
import sys
import wave
from typing import Any
from unittest.mock import patch
from urllib.error import URLError

import httpx
import pytest
import speech_recognition as sr  # type: ignore[import-untyped]
from fastapi import FastAPI, HTTPException
from speech_recognition.recognizers import google  # type: ignore[import-untyped]

from ucs_app.speech import GoogleTranscriber, MAX_BYTES, SpeechFailure, add_speech_routes, validate_wav
from ucs_app.speech_worker import recognize


def audio(seconds: float = 0.2, rate: int = 16000, sample: bytes = b'\x01\x00') -> bytes:
    output = io.BytesIO()
    with wave.open(output, 'wb') as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(sample * int(seconds * rate))
    return output.getvalue()


@pytest.mark.parametrize('data,code', [
    (b'not WAV', 415), (audio()[:-2], 415), (audio() + b'extra', 415),
    (audio(0.05), 422), (audio(30.1), 422), (audio(rate=4000), 415),
    (b'x' * (MAX_BYTES + 1), 413),
    (audio()[:20] + struct.pack('<H', 3) + audio()[22:], 415),
])
def test_actual_audio_validation(data: bytes, code: int) -> None:
    with pytest.raises(HTTPException) as error:
        validate_wav(data)
    assert error.value.status_code == code


def test_silence_and_supported_boundaries() -> None:
    with pytest.raises(SpeechFailure, match='silence'):
        validate_wav(audio(sample=b'\0\0'))
    validate_wav(audio(0.1, 8000))
    validate_wav(audio(30, 48000))


def test_real_recognizer_builds_https_flac_request() -> None:
    # Actual SpeechRecognition encoder, request builder and output parser; no network.
    payload = b'{"result":[{"alternative":[{"transcript":"elephant left"}]}]}\n'
    with patch.object(google, 'urlopen', return_value=io.BytesIO(payload)) as transport:
        assert recognize(audio(), 'en-AU') == {'transcript': 'elephant left'}
    request = transport.call_args.args[0]
    assert request.full_url.startswith('https://www.google.com/speech-api/v2/recognize?')
    assert 'lang=en-AU' in request.full_url
    assert request.data.startswith(b'fLaC')
    assert request.get_header('Content-type') == 'audio/x-flac; rate=16000'
    assert transport.call_args.kwargs == {'timeout': 10}


@pytest.mark.parametrize('failure,expected', [
    (sr.UnknownValueError('private'), 'unintelligible'),
    (sr.RequestError('private'), 'network'), (TimeoutError('private'), 'timeout'),
    (ValueError('private raw response'), 'unavailable'),
])
def test_recognizer_errors_are_sanitized(failure: Exception, expected: str) -> None:
    with patch.object(sr.Recognizer, 'recognize_google', side_effect=failure):
        assert recognize(audio(), 'en-AU') == {'error': expected}


def test_real_network_error_is_sanitized() -> None:
    with patch.object(google, 'urlopen', side_effect=URLError('private')):
        assert recognize(audio(), 'en-AU') == {'error': 'network'}


def test_endpoint_admission_and_no_workflow() -> None:
    async def scenario() -> None:
        gate = asyncio.Event()
        started = 0

        class Held:
            async def transcribe(self, wav: bytes) -> str:
                nonlocal started
                started += 1
                await gate.wait()
                return 'elephant left'

        app = FastAPI()
        add_speech_routes(app, lambda request: None, Held())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            async def send() -> httpx.Response:
                return await client.post('/speech/transcriptions', content=audio(), headers={'Content-Type': 'audio/wav'})
            tasks = [asyncio.create_task(send()) for _ in range(2)]
            while started < 2:
                await asyncio.sleep(0)
            assert (await send()).status_code == 429
            # Event loop remains responsive while both recognitions are waiting.
            assert (await client.get('/speech/speech.js')).status_code == 200
            gate.set()
            results = await asyncio.gather(*tasks)
            assert all(r.json() == {'transcript': 'elephant left'} for r in results)
            assert all(r.headers['cache-control'] == 'no-store' for r in results)
            assert (await send()).status_code == 200
            assert (await client.post('/speech/transcriptions', content=audio())).status_code == 415
            assert (await client.post('/speech/transcriptions', content=b'fake', headers={'Content-Type':'audio/wav'})).status_code == 415
            assert started == 3  # Invalid bytes never reach the recognizer.
    asyncio.run(scenario())


def test_worker_deadline_kills_process_and_keeps_loop_responsive() -> None:
    async def scenario() -> None:
        real_create = asyncio.create_subprocess_exec
        processes: list[asyncio.subprocess.Process] = []

        async def sleeper(*args: object, **kwargs: Any) -> asyncio.subprocess.Process:
            proc = await real_create(sys.executable, '-c', 'import time; time.sleep(60)', **kwargs)
            processes.append(proc)
            return proc

        adapter = GoogleTranscriber(timeout=0.15)
        with patch('ucs_app.speech.asyncio.create_subprocess_exec', side_effect=sleeper):
            task = asyncio.create_task(adapter.transcribe(audio()))
            await asyncio.sleep(0.03)
            assert not task.done()
            with pytest.raises(SpeechFailure, match='timeout'):
                await task
        assert processes[0].returncode is not None
    asyncio.run(scenario())


def test_worker_cancellation_reaps_process() -> None:
    async def scenario() -> None:
        real_create = asyncio.create_subprocess_exec
        processes: list[asyncio.subprocess.Process] = []

        async def sleeper(*args: object, **kwargs: Any) -> asyncio.subprocess.Process:
            proc = await real_create(sys.executable, '-c', 'import time; time.sleep(60)', **kwargs)
            processes.append(proc)
            return proc

        with patch('ucs_app.speech.asyncio.create_subprocess_exec', side_effect=sleeper):
            task = asyncio.create_task(GoogleTranscriber().transcribe(audio()))
            while not processes:
                await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert processes[0].returncode is not None
    asyncio.run(scenario())
