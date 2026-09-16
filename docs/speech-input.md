# Reviewed Google speech input (#5)

Speech supplies editable text to the existing request/answer field. It never calls
an agent, creates a workflow, publishes a command, or submits the form. The user
must review the text and select **Submit request** or **Send answer**. This does
not repair model interpretation or short-answer clarification-context failures.

## Setup and startup (Mac UCS)

```sh
.venv/bin/python -m pip install -e '.[dev]'
UCS_SPEECH_LANGUAGE=en-AU .venv/bin/uvicorn ucs_app.controlled:create_controlled_app --factory --host 127.0.0.1 --port 8002
```

Use an unused port for checking speech; preserve existing servers on 8000/8001.
The controlled composition keeps its existing simulation delay. For the live
composition, retain the existing Ollama URL/model settings and use
`ucs_app.live:create_live_app` instead. Speech runs on the Mac and makes no
Jetson requests. Coordinate a restart before adding speech to an existing demo
server. HTML served by an older process keeps the microphone controls hidden.
Run one Uvicorn worker for the stated two-transcription service-wide bound.

SpeechRecognition **3.14.6** is pinned. Python 3.9+ is supported. Browser capture
means **PyAudio/PortAudio are not required**. SpeechRecognition bundles a FLAC
encoder for Intel macOS; other platforms may need an installed `flac` executable.
There is no ffmpeg, browser speech-recognition API, or Google Cloud SDK dependency.
[Package requirements](https://pypi.org/project/SpeechRecognition/)

## Recording, validation and limits

1. Start microphone explicitly requests browser microphone permission and starts
   a mono AudioWorklet capture. Recording never starts at page load.
2. Stop microphone releases the device and sends an in-memory PCM16 WAV to UCS.
   At 30 seconds capture stops for safety; the user still presses Stop to send it.
   Cancelling a pending permission request sends nothing.
3. UCS validates actual RIFF/WAVE structure, PCM encoding, mono channels, 16-bit
   samples, 8–48 kHz sample rate, consistent byte/frame lengths, and 0.1–30 seconds.
   The upload limit is 2,880,044 bytes, including the 44-byte header. This deliberately
   accepts the recorder's canonical WAV only; arbitrary WAV variants, compressed
   audio, AIFF, FLAC uploads, WebM/Opus and MIME-only claims are rejected.
4. Two requests per server process may upload/transcribe concurrently; further
   requests receive 429 immediately. Uploads have a 10-second deadline. Each Google
   worker has a 15-second wall-clock deadline (encoding, DNS, transport and parsing
   included), with a 10-second recognizer network-operation timeout. Timeout or
   cancellation kills and reaps its process group, including its FLAC child.
   No blocking recognition runs on the application event loop.
5. Only a bounded transcript (1–4000 characters) returns with `Cache-Control:
   no-store`. Existing typed text is preserved and the transcript is appended.
   Any intervening edit, submission, cancellation or context change prevents late
   insertion. Errors retain the typed field. No recognition result submits itself.

## Actual transport and privacy

The installed recognizer's `recognize_google` builds a FLAC POST using Python
`urllib.request.urlopen`. Its default endpoint is **HTTP**, so UCS explicitly
passes `https://www.google.com/speech-api/v2/recognize`. Certificate validation is
left enabled. Deterministic tests inspect the actual request builder/encoder and
its HTTPS URL, FLAC body, language and timeout; that is not live network evidence.
`show_all=False` returns the selected text, not unrestricted alternatives.
[Recognizer implementation](https://github.com/Uberi/speech_recognition/blob/3.14.6/speech_recognition/recognizers/google.py)

Browser PCM WAV is chosen because SpeechRecognition supports PCM WAV/AIFF/native
FLAC, not browser MediaRecorder WebM/Opus output. The worker constructs AudioData
from validated PCM and the package converts it to FLAC in memory.
[Audio format reference](https://github.com/Uberi/speech_recognition/blob/3.14.6/reference/library-reference.rst)

The page discloses that Stop sends audio to Google. UCS writes no recordings or
raw recognition responses to files or logs; worker stderr is suppressed and
failures use fixed public messages. Temporary audio exists in browser/server
memory and process pipes only. Do not enable request-body logging at a proxy.
UCS cannot promise Google's retention policy. The generic key used by this
`recognize_google` service is intended for personal/testing use and can be revoked;
this is not the separately authenticated Google Cloud Speech-to-Text service.
[API limitations](https://github.com/Uberi/speech_recognition/blob/3.14.6/speech_recognition/recognizers/google.py)

## Browser and remote deployment

Microphone capture and AudioWorklet require a secure context. Localhost/loopback
HTTP is treated as trustworthy for local development; a remote Mac IP over plain
HTTP is not. A remote deployment needs a valid HTTPS origin (and HTTPS same-origin
uploads), microphone permission, and a proxy configured to avoid recording bodies
or caching transcription responses. Do not expose this local demo to an untrusted
network without access controls and deployment-level rate/body/time limits. An
iframe additionally needs appropriate microphone permissions from its parent.
[Microphone requirements](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia),
[AudioWorklet](https://developer.mozilla.org/en-US/docs/Web/API/AudioWorklet),
[Secure contexts](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Secure_Contexts)

## Verification and acceptance

```sh
.venv/bin/pytest tests/test_speech.py tests/test_speech_browser.py -q
.venv/bin/pytest -q
.venv/bin/mypy
```

Deterministic tests use generated audio and controlled adapter responses. They
cover validation, HTTPS request construction, public errors, worker termination,
concurrency, browser capture, editing races, and explicit request/answer submission.
They cannot establish microphone hardware quality, Google availability/accuracy,
live-model reliability or physical execution.

For separate live acceptance, open the isolated server on the Mac in a browser:

1. Enter a small typed prefix. Start microphone, grant permission, speak an
   arrangement, then Stop. Verify recording/transcribing states and Google text
   appended to the editable field; verify no activity/dispatch occurs.
2. Edit the result and explicitly Submit request only when ready. On the
   controlled composition any completion is **simulated execution**.
3. Separately repeat at an actual clarification using Send answer. During another
   transcription, edit the field and verify the late response does not overwrite it.
4. Record browser/OS, microphone permission outcome, transcription outcome, and
   application/model/execution outcomes separately. Do not save raw audio or private
   transcript contents by default. Permission-denied and silence checks are useful
   additional manual cases. A failed Google call is not a model failure.

Keep #5 open wherever live or deployment evidence is missing; record actual user
participation separately from deterministic tests. #4's outstanding live-model/
physical acceptance is not established by these tests.
