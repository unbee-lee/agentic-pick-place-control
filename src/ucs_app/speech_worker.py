"""Disposable Google recognition worker; stdout contains only bounded public output."""

import json
import sys

import speech_recognition as sr  # type: ignore[import-untyped]

from ucs_app.speech import GOOGLE_ENDPOINT, MAX_BYTES


def recognize(wav: bytes, language: str) -> dict[str, str]:
    try:
        recognizer = sr.Recognizer()
        recognizer.operation_timeout = 10
        rate = int.from_bytes(wav[24:28], "little")
        audio = sr.AudioData(wav[44:], rate, 2)
        text = recognizer.recognize_google(
            audio, language=language, endpoint=GOOGLE_ENDPOINT, show_all=False,
        )
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000:
            return {"error": "unintelligible"}
        return {"transcript": text.strip()}
    except sr.UnknownValueError:
        return {"error": "unintelligible"}
    except (TimeoutError,):
        return {"error": "timeout"}
    except sr.RequestError:
        return {"error": "network"}
    except Exception:
        # Never expose remote response bodies, URLs, credentials or raw audio.
        return {"error": "unavailable"}


if __name__ == "__main__":
    print(json.dumps(recognize(sys.stdin.buffer.read(MAX_BYTES + 1), sys.argv[1])))
