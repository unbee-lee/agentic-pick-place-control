"""Fake browser microphone + deterministic adapter; never live Google evidence."""

import asyncio
import socket
import math
import struct
import wave
from pathlib import Path
import threading
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Iterator, Mapping

import pytest
import uvicorn
from playwright.sync_api import Page, expect, sync_playwright

from ucs_app.app import create_app
from ucs_app.controlled import ControlledRobotCommandAgent, ControlledRobotExecutionStation, ControlledUserCommandAgent
from ucs_app.interfaces import AgentContext, ControllerUpdate
from ucs_app.speech import SpeechFailure, validate_wav


@dataclass
class Scenario:
    url: str = ''
    texts: list[str] = field(default_factory=lambda: ['elephant left, bear centre, hippo right'])
    recordings: int = 0
    interpretations: int = 0
    executions: int = 0
    release: threading.Event = field(default_factory=threading.Event)
    failure: str = ''

    async def transcribe(self, wav: bytes) -> str:
        validate_wav(wav)
        self.recordings += 1
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        if self.failure:
            raise SpeechFailure(self.failure)
        return self.texts.pop(0)


@pytest.fixture
def speech_site() -> Iterator[Scenario]:
    scenario = Scenario()
    scenario.release.set()

    class User(ControlledUserCommandAgent):
        async def decide(self, context: AgentContext) -> Mapping[str, object]:
            scenario.interpretations += 1
            return await super().decide(context)

    class RES(ControlledRobotExecutionStation):
        async def execute(self, command: Mapping[str, object]) -> AsyncIterator[ControllerUpdate]:
            scenario.executions += 1
            async for update in super().execute(command):
                yield update

    app = create_app(user_command_agent=User(), robot_command_agent=ControlledRobotCommandAgent(),
                     robot_execution_station=RES(result_delay_seconds=0.01), composition_label='Speech tests',
                     transcriber=scenario)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    scenario.url = f'http://127.0.0.1:{listener.getsockname()[1]}'
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    try:
        yield scenario
    finally:
        scenario.release.set()
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()


@pytest.fixture
def speech_page(speech_site: Scenario, tmp_path: Path) -> Iterator[Page]:
    tone = tmp_path / 'fake-microphone.wav'
    with wave.open(str(tone), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b''.join(struct.pack('<h', int(8000 * math.sin(i * 0.2))) for i in range(16000)))
    with sync_playwright() as p:
        browser = p.chromium.launch(args=['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', f'--use-file-for-fake-audio-capture={tone}'])
        page = browser.new_page()
        page.goto(speech_site.url)
        expect(page.get_by_test_id('ucs-status')).to_have_text('Ready')
        yield page
        browser.close()


def record(page: Page) -> None:
    page.get_by_test_id('speech-start').click()
    expect(page.get_by_test_id('speech-status')).to_have_attribute('data-state', 'recording')
    # Fake device has startup silence; collect its deterministic tone.
    page.wait_for_timeout(600)
    page.get_by_test_id('speech-stop').click()


def test_transcription_never_submits_or_dispatches(speech_site: Scenario, speech_page: Page) -> None:
    page = speech_page
    record(page)
    expect(page.get_by_test_id('speech-status')).to_contain_text('Transcript added')
    expect(page.get_by_test_id('arrangement-request')).to_have_value('elephant left, bear centre, hippo right')
    expect(page.get_by_test_id('ucs-status')).to_have_text('Ready')
    assert speech_site.recordings == 1
    assert speech_site.interpretations == speech_site.executions == 0
    page.get_by_role('button', name='Submit request', exact=True).click()
    expect(page.get_by_test_id('ucs-status')).to_have_text('Completed')
    assert speech_site.interpretations == speech_site.executions == 1


def test_transcribed_clarification_still_requires_send_answer(speech_site: Scenario, speech_page: Page) -> None:
    speech_site.texts = ['bear centre, hippo right']
    page = speech_page
    page.get_by_test_id('arrangement-request').fill('elephant left')
    page.get_by_role('button', name='Submit request', exact=True).click()
    expect(page.get_by_test_id('ucs-status')).to_have_text('Needs clarification')
    record(page)
    expect(page.get_by_test_id('speech-status')).to_contain_text('Transcript added')
    assert speech_site.interpretations == 1
    assert speech_site.executions == 0
    page.get_by_role('button', name='Send answer', exact=True).click()
    expect(page.get_by_test_id('ucs-status')).to_have_text('Completed')
    assert speech_site.executions == 1


@pytest.mark.parametrize('change', ['edit', 'submit', 'cancel'])
def test_late_transcript_preserves_new_input_or_context(speech_site: Scenario, speech_page: Page, change: str) -> None:
    page = speech_page
    field = page.get_by_test_id('arrangement-request')
    if change == 'cancel':
        field.fill('elephant left')
        page.get_by_role('button', name='Submit request', exact=True).click()
        expect(page.get_by_test_id('ucs-status')).to_have_text('Needs clarification')
    speech_site.release.clear()
    record(page)
    expect(page.get_by_test_id('speech-status')).to_contain_text('Transcribing')
    if change == 'edit':
        field.fill('my newer typed request')
    elif change == 'submit':
        field.fill('elephant right, bear centre, hippo left')
        page.get_by_role('button', name='Submit request', exact=True).click()
        expect(page.get_by_test_id('ucs-status')).to_have_text('Completed')
    else:
        page.get_by_role('button', name='Cancel request', exact=True).click()
        expect(page.get_by_test_id('ucs-status')).to_have_text('Cancelled')
    previous = field.input_value()
    speech_site.release.set()
    expect(page.get_by_test_id('speech-status')).to_contain_text('late transcript was not inserted')
    expect(field).to_have_value(previous)
    assert speech_site.executions == (1 if change == 'submit' else 0)


def test_existing_text_is_preserved_and_appended(speech_site: Scenario, speech_page: Page) -> None:
    page = speech_page
    page.get_by_test_id('arrangement-request').fill('Please arrange:')
    record(page)
    expect(page.get_by_test_id('arrangement-request')).to_have_value('Please arrange: elephant left, bear centre, hippo right')
    assert speech_site.interpretations == speech_site.executions == 0


@pytest.mark.parametrize('failure,description', [('silence', 'No speech'), ('unintelligible', 'not understood'),
                                                ('network', 'unavailable'), ('timeout', 'timed out')])
def test_errors_preserve_typed_input(speech_site: Scenario, speech_page: Page, failure: str, description: str) -> None:
    speech_site.failure = failure
    speech_page.get_by_test_id('arrangement-request').fill('keep this text')
    record(speech_page)
    expect(speech_page.get_by_test_id('speech-status')).to_contain_text(description)
    expect(speech_page.get_by_test_id('arrangement-request')).to_have_value('keep this text')
    assert speech_site.interpretations == speech_site.executions == 0


@pytest.mark.parametrize('name,message', [('NotAllowedError', 'permission was denied'), ('NotFoundError', 'No microphone'),
                                         ('NotReadableError', 'unavailable or in use')])
def test_microphone_errors(speech_site: Scenario, speech_page: Page, name: str, message: str) -> None:
    speech_page.evaluate('(name) => { navigator.mediaDevices.getUserMedia = async () => {throw new DOMException("test", name)}; }', name)
    speech_page.get_by_test_id('speech-start').click()
    expect(speech_page.get_by_test_id('speech-status')).to_contain_text(message)
    assert speech_site.recordings == speech_site.interpretations == speech_site.executions == 0


def test_capture_limit_does_not_send_without_stop(speech_site: Scenario, speech_page: Page) -> None:
    # AudioWorklet requests are not reliably intercepted by Playwright routes.
    # Exercise the actual 30-second bound rather than a mocked recording timer.
    speech_page.get_by_test_id('speech-start').click()
    expect(speech_page.get_by_test_id('speech-status')).to_have_attribute('data-state', 'limit', timeout=35000)
    assert speech_site.recordings == speech_site.interpretations == speech_site.executions == 0
    speech_page.get_by_test_id('speech-stop').click()
    expect(speech_page.get_by_test_id('speech-status')).to_contain_text('Transcript added')
    assert speech_site.recordings == 1
    assert speech_site.interpretations == speech_site.executions == 0


def test_upload_network_failure_does_not_submit(speech_site: Scenario, speech_page: Page) -> None:
    speech_page.route('**/speech/transcriptions', lambda route: route.abort())
    record(speech_page)
    expect(speech_page.get_by_test_id('speech-status')).to_contain_text('Check your connection')
    assert speech_site.recordings == speech_site.interpretations == speech_site.executions == 0


def test_pending_permission_can_be_cancelled(speech_site: Scenario, speech_page: Page) -> None:
    speech_page.evaluate('''() => {
      navigator.mediaDevices.getUserMedia = () => new Promise(() => {});
    }''')
    speech_page.get_by_test_id('speech-start').click()
    expect(speech_page.get_by_test_id('speech-status')).to_contain_text('Waiting for microphone permission')
    speech_page.get_by_test_id('speech-stop').click()
    expect(speech_page.get_by_test_id('speech-status')).to_contain_text('Nothing was sent')
    expect(speech_page.get_by_test_id('speech-start')).to_be_enabled()
    assert speech_site.recordings == speech_site.interpretations == speech_site.executions == 0
