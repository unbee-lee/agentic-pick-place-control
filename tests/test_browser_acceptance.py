"""Browser-observable acceptance test for the controlled UCS composition."""

import asyncio
import socket
import threading
import time
from typing import AsyncIterator, Iterator, Mapping
from urllib.request import urlopen

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from ucs_app import create_app
from ucs_app.controlled import ControlledRobotCommandAgent, ControlledRobotExecutionStation, ControlledUserCommandAgent
from ucs_app.interfaces import AgentContext, ControllerUpdate


@pytest.mark.parametrize("controlled_ucs_url,expected", [
    ("success", "Completed"), ("failed", "Failed"), ("missing", "Outcome unknown"),
], indirect=["controlled_ucs_url"])
def test_demo_photo_tracks_only_matching_completion(controlled_ucs_url: str, expected: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        # Private presentation photographs are not required by the test suite.
        page.route("**/demo-images/*.jpg", lambda route: route.fulfill(
            content_type="image/svg+xml",
            body='<svg xmlns="http://www.w3.org/2000/svg" width="4" height="3"><rect width="4" height="3"/></svg>',
        ))
        page.goto(controlled_ucs_url)
        expect(page.get_by_test_id("ucs-status")).to_have_text("Ready")
        expect(page.get_by_test_id("demo-before")).to_be_visible()
        expect(page.get_by_test_id("demo-after")).to_be_hidden()
        page.get_by_test_id("arrangement-request").fill("elephant left")
        page.get_by_role("button", name="Submit request").click()
        expect(page.get_by_test_id("ucs-status")).to_have_text("Needs clarification")
        expect(page.get_by_test_id("demo-after")).to_be_hidden()
        page.get_by_test_id("arrangement-request").fill("bear centre, hippo right")
        page.get_by_role("button", name="Send answer").click()
        expect(page.get_by_test_id("ucs-status")).to_have_text(expected)
        if expected == "Completed":
            expect(page.get_by_test_id("demo-after")).to_be_visible()
            page.get_by_test_id("arrangement-request").fill("elephant right, bear centre, hippo left")
            page.get_by_role("button", name="Submit request").click()
            expect(page.get_by_test_id("demo-after")).to_be_hidden()
            expect(page.get_by_test_id("ucs-status")).to_have_text("Completed")
            expect(page.get_by_test_id("demo-after-message")).to_contain_text("No matching demo photo")
        else:
            expect(page.get_by_test_id("demo-after")).to_be_hidden()
        page.reload()
        expect(page.get_by_test_id("demo-after")).to_be_hidden()
        browser.close()


@pytest.fixture
def controlled_ucs_url(request: pytest.FixtureRequest) -> Iterator[str]:
    """Run the controlled UCS composition on an available local port."""

    host = "127.0.0.1"
    with socket.socket() as listener:
        listener.bind((host, 0))
        port = listener.getsockname()[1]

    mode = getattr(request, "param", "success")
    release_execution = threading.Event()

    class ScenarioRES(ControlledRobotExecutionStation):
        async def execute(self, command: Mapping[str, object]) -> AsyncIterator[ControllerUpdate]:
            async for update in super().execute(command):
                if update.message_type == "result":
                    if mode == "held":
                        while not release_execution.is_set():
                            await asyncio.sleep(0.01)
                    if mode == "missing":
                        return
                    if mode == "failed":
                        payload = dict(update.payload)
                        payload["execution"] = {"status": "FAILED", "error": {
                            "code": "EXECUTION_FAILED", "message": "Controlled failure",
                        }}
                        yield ControllerUpdate("result", payload)
                        continue
                yield update

    class ScenarioRobot(ControlledRobotCommandAgent):
        calls = 0

        async def decide(self, context: AgentContext) -> Mapping[str, object]:
            self.calls += 1
            if mode in ("repair", "exhaust") and (self.calls == 1 or mode == "exhaust"):
                return {"action": "VALIDATE", "command": {}}
            return await super().decide(context)

    application = create_app(
        user_command_agent=ControlledUserCommandAgent(), robot_command_agent=ScenarioRobot(),
        robot_execution_station=ScenarioRES(result_delay_seconds=0.75), composition_label="Controlled development",
    )
    @application.post("/test/release")
    async def release() -> dict[str, bool]:
        release_execution.set()
        return {"released": True}

    server = uvicorn.Server(
        uvicorn.Config(application, host=host, port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://{host}:{port}"
    for _ in range(100):
        try:
            with urlopen(f"{base_url}/health", timeout=0.1) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(0.05)
    else:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("controlled UCS did not start")

    yield base_url

    release_execution.set()
    server.should_exit = True
    thread.join(timeout=5)
    if thread.is_alive():
        raise RuntimeError("controlled UCS did not stop")


def test_clarification_answers_restore_and_complete_restatement_recovers(controlled_ucs_url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(controlled_ucs_url)
        expect(page.get_by_test_id("ucs-status")).to_have_text("Ready")
        page.get_by_test_id("arrangement-request").fill("please arrange the animals")
        page.get_by_role("button", name="Submit request").click()
        expect(page.get_by_test_id("ucs-status")).to_have_text("Needs clarification")
        answer = '<img src=x onerror="window.injected=true">'
        page.get_by_test_id("arrangement-request").fill(answer)
        page.get_by_role("button", name="Send answer").click()
        expect(page.get_by_test_id("clarification-answers").locator("p")).to_have_text([f"Clarification answer: {answer}"])
        expect(page.get_by_test_id("clarification-context")).to_contain_text("please arrange the animals")
        assert page.get_by_test_id("clarification-answers").locator("img").count() == 0
        page.reload()
        expect(page.get_by_test_id("ucs-status")).to_have_text("Needs clarification")
        expect(page.get_by_test_id("clarification-answers").locator("p")).to_have_text([f"Clarification answer: {answer}"])
        page.get_by_test_id("arrangement-request").fill("elephant left, bear center and hippo right")
        page.get_by_role("button", name="Send answer").click()
        expect(page.get_by_test_id("ucs-status")).to_have_text("Completed")
        expect(page.locator('[data-event-kind="command_publication"]')).to_have_count(1)
        page.get_by_test_id("arrangement-request").fill("elephant left")
        page.get_by_role("button", name="Submit request").click()
        expect(page.get_by_test_id("ucs-status")).to_have_text("Needs clarification")
        expect(page.get_by_test_id("clarification-answers").locator("p")).to_have_count(0)
        browser.close()


def test_valid_target_is_delivered_without_confirmation(controlled_ucs_url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(controlled_ucs_url)
        expect(page.get_by_test_id("ucs-status")).to_have_text("Ready")
        page.get_by_test_id("arrangement-request").fill("elephant left, bear centre, hippo right")
        page.get_by_role("button", name="Submit request").click()
        expect(page.get_by_test_id("validated-target")).to_be_visible()
        expect(page.get_by_test_id("target-E")).to_contain_text("front left")
        expect(page.get_by_role("button", name="Confirm", exact=True)).to_have_count(0)
        expect(page.get_by_test_id("ucs-status")).to_have_text("Completed")
        expect(page.get_by_test_id("status-description")).to_contain_text("Physical placement was not verified")
        expect(page.get_by_role("status")).to_have_count(1)
        expect(page.get_by_test_id("execution-result")).to_have_count(0)
        expect(page.get_by_test_id("verification-result")).to_have_count(0)
        expect(page.get_by_test_id("connection-status")).to_have_count(0)
        expect(page.locator('[data-event-kind="command_publication"]')).to_have_count(1)
        expect(page.get_by_role("button", name="Submit request")).to_be_enabled()
        browser.close()


def test_invalid_target_returns_to_user_then_dispatches_corrected_target(controlled_ucs_url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(controlled_ucs_url)
        expect(page.get_by_test_id("ucs-status")).to_have_text("Ready")
        page.get_by_test_id("arrangement-request").fill("elephant left, bear left, hippo right")
        page.get_by_role("button", name="Submit request").click()
        expect(page.get_by_test_id("clarification-panel")).to_be_visible()
        expect(page.get_by_test_id("clarification-question")).to_be_visible()
        expect(page.get_by_label("Your answer", exact=True)).to_be_focused()
        expect(page.get_by_test_id("ucs-status")).to_have_text("Needs clarification")
        expect(page.locator('[data-event-kind="command_publication"]')).to_have_count(0)
        expect(page.get_by_role("button", name="Send answer")).to_be_enabled()
        page.get_by_test_id("arrangement-request").fill("bear centre")
        page.get_by_role("button", name="Send answer").click()
        expect(page.get_by_test_id("ucs-status")).to_have_text("Completed")
        expect(page.get_by_test_id("target-B")).to_contain_text("front center")
        expect(page.locator('[data-event-kind="input"]')).to_have_count(2)
        expect(page.locator('[data-event-kind="clarification"]')).to_have_count(1)
        expect(page.locator('[data-event-kind="command_publication"]')).to_have_count(1)
        browser.close()


def test_missing_information_can_be_cancelled_without_delivery(controlled_ucs_url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(controlled_ucs_url)
        expect(page.get_by_test_id("ucs-status")).to_have_text("Ready")
        page.get_by_test_id("arrangement-request").fill("elephant left")
        page.get_by_role("button", name="Submit request").click()
        expect(page.get_by_test_id("clarification-panel")).to_be_visible()
        expect(page.get_by_test_id("clarification-question")).to_be_visible()
        expect(page.get_by_label("Your answer", exact=True)).to_be_focused()
        expect(page.get_by_test_id("ucs-status")).to_have_text("Needs clarification")
        page.get_by_role("button", name="Cancel request").click()
        expect(page.get_by_test_id("clarification-question")).to_be_hidden()
        expect(page.get_by_test_id("ucs-status")).to_have_text("Cancelled")
        expect(page.locator('[data-event-kind="command_publication"]')).to_have_count(0)
        expect(page.get_by_role("button", name="Submit request")).to_be_enabled()
        browser.close()


@pytest.mark.parametrize("controlled_ucs_url,expected", [("failed", "Failed"), ("missing", "Outcome unknown")], indirect=["controlled_ucs_url"])
def test_one_status_reports_unsuccessful_outcomes(controlled_ucs_url: str, expected: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(controlled_ucs_url)
        expect(page.get_by_test_id("ucs-status")).to_have_text("Ready")
        page.get_by_test_id("arrangement-request").fill("elephant left, bear centre, hippo right")
        page.get_by_role("button", name="Submit request").click()
        expect(page.get_by_test_id("ucs-status")).to_have_text("Executing")
        expect(page.get_by_test_id("ucs-status")).to_have_text(expected)
        expect(page.get_by_role("status")).to_have_count(1)
        expect(page.get_by_test_id("status-description")).not_to_contain_text("Simulation completed")
        if expected == "Outcome unknown":
            expect(page.get_by_role("button", name="Submit request")).to_be_disabled()
        browser.close()


def test_new_request_resets_activity_but_keeps_current_request_steps(controlled_ucs_url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(controlled_ucs_url)
        expect(page.get_by_test_id("ucs-status")).to_have_text("Ready")
        expect(page.get_by_role("heading", name="User Command Station")).to_be_visible()
        expect(page.get_by_text("Controlled development", exact=False)).to_have_count(0)
        for text, expected in [
            ("elephant left, bear centre, hippo right", "front left"),
            ("elephant right, bear centre, hippo left", "front right"),
        ]:
            page.get_by_label("Your request", exact=True).fill(text)
            page.get_by_role("button", name="Submit request").click()
            expect(page.get_by_test_id("target-E")).to_contain_text(expected)
            expect(page.get_by_test_id("ucs-status")).to_have_text("Completed")
            expect(page.get_by_role("button", name="Submit request")).to_be_enabled()
            expect(page.locator('[data-event-kind="input"]')).to_have_count(1)
            expect(page.locator('[data-event-kind="command_publication"]')).to_have_count(1)
            expect(page.locator('[data-event-kind="result"]')).to_have_count(1)
        browser.close()


def test_refresh_pending_clarification_keeps_request_usable(controlled_ucs_url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(controlled_ucs_url)
        expect(page.get_by_test_id("ucs-status")).to_have_text("Ready")
        page.get_by_test_id("arrangement-request").fill("elephant left")
        page.get_by_role("button", name="Submit request").click()
        expect(page.get_by_test_id("ucs-status")).to_have_text("Needs clarification")
        expect(page.get_by_role("button", name="Send answer")).to_be_enabled()
        page.reload()
        expect(page.locator('button[type="submit"]')).to_be_enabled()
        page.get_by_test_id("arrangement-request").fill("elephant left, bear centre, hippo right")
        with page.expect_response(lambda response: response.request.method == "POST") as submitted:
            page.locator('button[type="submit"]').click()
        response = submitted.value
        assert response.status == 202, response.json()
        expect(page.get_by_test_id("ucs-status")).to_have_text("Completed")
        expect(page.locator('[data-event-kind="command_publication"]')).to_have_count(1)
        browser.close()


def test_refresh_restores_question_and_allows_cancel_then_new_request(controlled_ucs_url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(controlled_ucs_url)
        expect(page.get_by_role("button", name="Submit request")).to_be_enabled()
        page.get_by_test_id("arrangement-request").fill("elephant left")
        page.get_by_role("button", name="Submit request").click()
        expect(page.get_by_role("button", name="Send answer")).to_be_enabled()
        page.reload()
        expect(page.get_by_test_id("ucs-status")).to_have_text("Needs clarification")
        expect(page.get_by_test_id("clarification-context")).to_contain_text("elephant left")
        expect(page.locator('[data-event-kind="clarification"]')).to_have_count(1)
        page.get_by_role("button", name="Cancel request").click()
        expect(page.get_by_test_id("ucs-status")).to_have_text("Cancelled")
        page.get_by_test_id("arrangement-request").fill("elephant right, bear centre, hippo left")
        page.get_by_role("button", name="Submit request").click()
        expect(page.get_by_test_id("ucs-status")).to_have_text("Completed")
        page.reload()
        expect(page.get_by_test_id("ucs-status")).to_have_text("Ready")
        expect(page.get_by_test_id("validated-target")).to_be_hidden()
        expect(page.get_by_test_id("activity-trail").locator("li")).to_have_count(0)
        expect(page.get_by_label("Your request", exact=True)).to_have_value("")
        expect(page.get_by_role("button", name="Submit request")).to_be_enabled()
        page.get_by_label("Your request", exact=True).fill("elephant left, bear centre, hippo right")
        page.get_by_role("button", name="Submit request").click()
        expect(page.get_by_test_id("ucs-status")).to_have_text("Completed")
        expect(page.get_by_test_id("target-E")).to_contain_text("front left")
        expect(page.locator('[data-event-kind="command_publication"]')).to_have_count(1)
        browser.close()


@pytest.mark.parametrize("controlled_ucs_url", ["held"], indirect=True)
def test_refresh_during_execution_restores_busy_guard(controlled_ucs_url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(controlled_ucs_url)
        expect(page.get_by_role("button", name="Submit request")).to_be_enabled()
        page.get_by_test_id("arrangement-request").fill("elephant left, bear centre, hippo right")
        page.get_by_role("button", name="Submit request").click()
        expect(page.get_by_test_id("ucs-status")).to_have_text("Executing")
        page.reload()
        expect(page.get_by_test_id("ucs-status")).to_have_text("Executing")
        expect(page.get_by_role("button", name="Submit request")).to_be_disabled()
        page.request.post(controlled_ucs_url + "/test/release")
        expect(page.get_by_test_id("ucs-status")).to_have_text("Completed")
        expect(page.get_by_role("button", name="Submit request")).to_be_enabled()
        expect(page.locator('[data-event-kind="command_publication"]')).to_have_count(1)
        browser.close()


@pytest.mark.parametrize("controlled_ucs_url", ["missing"], indirect=True)
def test_refresh_cannot_clear_unknown_execution(controlled_ucs_url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(controlled_ucs_url)
        expect(page.get_by_role("button", name="Submit request")).to_be_enabled()
        page.get_by_test_id("arrangement-request").fill("elephant left, bear centre, hippo right")
        page.get_by_role("button", name="Submit request").click()
        expect(page.get_by_test_id("ucs-status")).to_have_text("Outcome unknown")
        page.reload()
        expect(page.get_by_test_id("ucs-status")).to_have_text("Outcome unknown")
        expect(page.get_by_role("button", name="Submit request")).to_be_disabled()
        expect(page.locator('[data-event-kind="command_publication"]')).to_have_count(1)
        browser.close()


@pytest.mark.parametrize("controlled_ucs_url,expected", [("repair", "Completed"), ("exhaust", "Failed")], indirect=["controlled_ucs_url"])
def test_command_repair_has_no_user_confirmation_or_internal_error_question(controlled_ucs_url: str, expected: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(controlled_ucs_url)
        expect(page.get_by_test_id("ucs-status")).to_have_text("Ready")
        page.get_by_test_id("arrangement-request").fill("elephant left, bear centre, hippo right")
        page.get_by_role("button", name="Submit request").click()
        expect(page.get_by_test_id("ucs-status")).to_have_text(expected)
        expect(page.locator('[data-event-kind="clarification"]')).to_have_count(0)
        expect(page.locator('[data-event-kind="command_publication"]')).to_have_count(1 if expected == "Completed" else 0)
        expect(page.get_by_role("button", name="Confirm", exact=True)).to_have_count(0)
        browser.close()
