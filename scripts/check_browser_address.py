"""Manual, model-directed staging address round trip; no staff-chat write tools."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from dotenv import dotenv_values
from openai import OpenAI
from openai.types.responses import ResponseInputParam
from playwright.sync_api import Locator, Page, Route
from scripts.address_via_browser import SLUG
from scripts.check_staging import UI_URL, staging_browser

from dental_practice_admin.config import Environment, Settings

PATIENT = "Annette Dummy"
PATIENT_ID = "Ab00hnl8R3EZpPHzcB0G"
ADDRESS = "pr-patient-address"
STREET = "pr-address-input [formcontrolname=streetName]"
WORKSPACE_PREFIX = f"/{SLUG}/"
PATIENTS_URL = UI_URL + WORKSPACE_PREFIX + "patients"
EXCLUDED = ("r6YoXCruTjwciIoidQy3", "NYiQ7JuG7SebVoDPzZZK")
PROCEDURE = Path(__file__).with_name("browser_address_procedure.md")
MAX_CALLS = 40
PHASE_SECONDS = 300
TOOL: Any = {
    "type": "function", "name": "browser", "strict": True,
    "description": (
        "Operate the staging browser. selector is CSS or text=exact visible text. "
        "Actions: click, fill, observe, reload, navigate, capture, verify. "
        "navigate takes a staging browser URL in text. "
        "capture requires pr-patient-address and patient_id from the URL. "
        "verify requires pr-patient-address after reloading with the edit dialog closed. "
        "Unused arguments must be empty strings."
    ),
    "parameters": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": [
                "click", "fill", "observe", "reload", "navigate", "capture", "verify",
            ]},
            "selector": {"type": "string"}, "text": {"type": "string"},
            "patient_id": {"type": "string"},
        },
        "required": ["action", "selector", "text", "patient_id"],
    },
}


def check_location(url: str, patient_id: str = "") -> None:
    """Keep the browser on staging and away from the other session's records."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != urlsplit(UI_URL).netloc:
        raise ValueError("Browser left staging")
    if any(record in url for record in EXCLUDED):
        raise ValueError("Patient belongs to another test")
    if patient_id and patient_id not in url:
        raise ValueError("Browser left the selected patient")


class BrowserTest:
    """Small UI executor with runner-owned address capture and assertions."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.patient_id = ""
        self.original: str | None = None
        self.address_selector = ADDRESS
        self.phase = "discovery"
        self.expected = ""
        self.reloaded = False
        self.complete = False
        self.test_value = "CUA TEST " + time.strftime("%Y%m%d-%H%M%S", time.gmtime())

    def locator(self, selector: str) -> Locator:
        """Resolve one visible UI element, without executing model-provided code."""
        if selector.startswith("text="):
            target = self.page.get_by_text(selector[5:], exact=True)
        else:
            target = self.page.locator(selector)
        if target.count() != 1 or not target.is_visible():
            raise ValueError("Selector must identify exactly one visible element")
        return target

    def saved_address(self) -> str:
        """Read persisted Profile text with no unsaved patient form open."""
        if self.page.locator("pr-update-patient:visible").count():
            raise ValueError("Close the edit dialog before reading the saved address")
        return self.locator(ADDRESS).inner_text().strip().removeprefix("Address:").strip()

    def observation(self) -> list[dict[str, Any]]:
        """Return rendered UI only; credentials and network state are unavailable."""
        check_location(self.page.url, self.patient_id)
        if self.page.locator('input[type="password"]:visible').count():
            raise ValueError("Login screen cannot be sent to the model")
        fields = self.page.locator("input, textarea, select").evaluate_all(
            "els => els.filter(e => e.getClientRects().length).map(e => ({"
            "tag:e.tagName,id:e.id,name:e.name,type:e.type,placeholder:e.placeholder,"
            "formcontrolname:e.getAttribute('formcontrolname'),"
            "ariaLabel:e.getAttribute('aria-label'),value:e.value}))"
        )
        return [
            {"type": "input_text", "text": json.dumps({
                "url": self.page.url, "page": self.page.locator("body").aria_snapshot(),
                "fields": fields,
            })},
            {"type": "input_image", "detail": "original", "image_url":
             "data:image/png;base64," + base64.b64encode(self.page.screenshot()).decode()},
        ]

    def execute(self, args: dict[str, str]) -> None:
        """Execute a bounded UI action and independently verify checkpoints."""
        check_location(self.page.url, self.patient_id)
        action, selector = args["action"], args["selector"]
        if action == "navigate":
            check_location(args["text"], self.patient_id)
            if not urlsplit(args["text"]).path.startswith(WORKSPACE_PREFIX):
                raise ValueError("Navigation must remain in the Massey Smiles workspace")
            self.page.goto(args["text"], wait_until="domcontentloaded")
            self.reloaded = False
            return
        if action == "observe":
            self.page.wait_for_timeout(750)
            return
        if action == "reload":
            self.page.reload(wait_until="domcontentloaded")
            self.page.wait_for_timeout(1000)
            self.reloaded = True
            return
        if action == "capture":
            if self.phase != "discovery" or self.original is not None:
                raise ValueError("Capture is only allowed once")
            patient_id = args["patient_id"]
            if not re.fullmatch(r"[A-Za-z0-9]{20}", patient_id):
                raise ValueError("Expected the patient ID from the URL")
            check_location(self.page.url, patient_id)
            if not urlsplit(self.page.url).path.startswith(WORKSPACE_PREFIX):
                raise ValueError("Patient must be in the Massey Smiles workspace")
            body = self.page.locator("body").inner_text()
            if patient_id != PATIENT_ID or not re.search(r"Annette(?: \(Annie\))? Dummy", body):
                raise ValueError("Expected Annette Dummy's confirmed staging identity")
            if selector != ADDRESS:
                raise ValueError("Capture must read the saved Profile address")
            self.original = self.saved_address()
            self.patient_id = patient_id
            self.complete = True
            return
        if action == "verify":
            if self.phase == "discovery" or not self.reloaded:
                raise ValueError("Verification requires a fresh page load")
            if selector != self.address_selector:
                raise ValueError("Verification must read the captured address field")
            value = self.saved_address()
            if value != self.expected:
                raise ValueError("Persisted address does not equal the expected value")
            self.complete = True
            return
        target = self.locator(selector)
        if action == "fill":
            if self.phase == "discovery":
                if args["text"] not in ("", PATIENT, *PATIENT.split(), "Massey Smiles Dental"):
                    raise ValueError("Only the target patient search is allowed")
            elif self.original is None or selector != STREET or args["text"] != self.expected:
                raise ValueError("Only the captured address field and expected value may be filled")
            target.fill("")
            target.press_sequentially(args["text"], delay=40)
            self.reloaded = False
            return
        if action == "click":
            label = target.inner_text().strip().lower()
            if re.search(r"\b(delete|send)\b", label):
                raise ValueError("Deleting and sending are forbidden")
            if self.phase == "discovery" and re.search(r"\b(save|submit|update patient)\b", label):
                raise ValueError("Discovery cannot save or send")
            if self.phase != "discovery" and label == "edit":
                allowed = (self.original,) if self.phase == "edit" else (
                    self.original, self.test_value,
                )
                if self.saved_address() not in allowed:
                    raise ValueError("Address changed outside this test")
            target.click()
            self.page.wait_for_timeout(500)
            return
        raise ValueError("Unsupported browser action")


def run_phase(client: OpenAI, model: str, browser: BrowserTest, stats: dict[str, Any]) -> None:
    """Let the model choose each action from English instructions and current UI."""
    browser.complete = False
    browser.reloaded = False
    history: Any = [{"role": "user", "content": (
        PROCEDURE.read_text(encoding="utf-8").replace("{patient_name}", PATIENT)
        .replace("{patients_url}", PATIENTS_URL)
        + "\nCurrent phase: " + browser.phase
        + "\nExpected address value: " + json.dumps(browser.expected)
        + "\nOriginal address: " + json.dumps(browser.original)
        + "\nTest value: " + browser.test_value
    )}]
    deadline = time.monotonic() + PHASE_SECONDS
    previous_action: dict[str, str] | None = None
    repeats = 0
    for step in range(MAX_CALLS):
        if time.monotonic() >= deadline:
            raise TimeoutError("Phase time budget exhausted")
        observation = browser.observation()
        history.append({"role": "user", "content": [observation[0]]})
        response = client.responses.create(
            model=model, store=False, tools=[TOOL], parallel_tool_calls=False,
            input=cast(ResponseInputParam, [
                *history, {"role": "user", "content": [observation[1]]},
            ]),
        )
        stats["model_calls"] += 1
        if response.usage:
            stats["input_tokens"] += response.usage.input_tokens
            stats["output_tokens"] += response.usage.output_tokens
        history.extend(item.model_dump(exclude_none=True) for item in response.output)
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            if stats["diagnostic"]:
                print(json.dumps({"model_stop_reason": response.output_text}), flush=True)
                print(browser.page.locator("body").aria_snapshot(), flush=True)
            raise RuntimeError("Model stopped without verified completion")
        for call in calls:
            args = json.loads(call.arguments)
            repeats = repeats + 1 if args == previous_action else 1
            previous_action = args
            if repeats >= 3:
                raise RuntimeError("Repeated browser action without progress; inspect the UI")
            try:
                browser.execute(args)
                outcome = "Completed action."
            except Exception as error:
                outcome = type(error).__name__ + ": " + str(error).splitlines()[0]
            print(json.dumps({"phase": browser.phase, "step": step + 1,
                              "action": args["action"], "ok": outcome == "Completed action.",
                              "outcome": outcome}),
                  flush=True)
            if stats["diagnostic"]:
                print(json.dumps({"selector": args["selector"], "url": browser.page.url}),
                      flush=True)
                if args["action"] == "capture" and browser.complete:
                    print(browser.observation()[0]["text"], flush=True)
            history.append({"type": "function_call_output", "call_id": call.call_id,
                            "output": outcome})
        if browser.complete:
            return
    if stats["diagnostic"]:
        print(browser.page.locator("body").aria_snapshot(), flush=True)
    raise TimeoutError("Phase call budget exhausted")


def guard_navigation(route: Route) -> None:
    """Restrict documents to staging, allowing Firebase's embedded sign-in frame."""
    if not route.request.is_navigation_request():
        route.continue_()
        return
    embedded = route.request.frame != route.request.frame.page.main_frame
    if embedded and urlsplit(route.request.url).netloc == "principle-staging.firebaseapp.com":
        route.continue_()
        return
    try:
        check_location(route.request.url)
    except ValueError:
        route.abort()
        return
    route.continue_()


def main() -> int:
    """Run an explicit staging-only experiment and print patient-free results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ui-env-file", type=Path, help="Defaults to --env-file")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--diagnostic", action="store_true", help="Print UI text on model stop")
    parser.add_argument(
        "--discovery-only", action="store_true", help="Find and inspect, never save",
    )
    args = parser.parse_args()
    settings_options: dict[str, Any] = {
        "_env_file": args.env_file, "environment": Environment.STAGING,
    }
    settings = Settings(**settings_options)
    values = {**dotenv_values(args.ui_env_file or args.env_file), **os.environ}
    email, password = values["PRINCIPLE_UI_EMAIL"], values["PRINCIPLE_UI_PASSWORD"]
    if not email or not password or not settings.openai_api_key.get_secret_value():
        raise ValueError("Staging UI and OpenAI credentials are required")
    client = OpenAI(api_key=settings.openai_api_key.get_secret_value(),
                    base_url=settings.openai_base_url or None, timeout=60, max_retries=0)
    stats: dict[str, Any] = {"model": settings.agent_model, "diagnostic": args.diagnostic,
                             "model_calls": 0,
                             "input_tokens": 0, "output_tokens": 0,
                             "edit_verified": False, "restoration_verified": False}
    started = time.monotonic()
    try:
        with staging_browser() as session:
            stats["stage"] = "login"
            session.page.set_default_timeout(30000)
            session.login(email, password, redirect_to=WORKSPACE_PREFIX + "patients")
            # Firestore Listen bodies can remain open; this experiment only observes rendered UI.
            session.page.remove_listener("response", session._observe)
            session.page.get_by_placeholder("Password", exact=True).wait_for(
                state="hidden", timeout=30000,
            )
            session.page.context.route("**/*", guard_navigation)
            session.page.set_default_timeout(8000)
            stats["stage"] = "discovery"
            browser = BrowserTest(session.page)
            run_phase(client, settings.agent_model, browser, stats)
            stats["discovery_verified"] = True
            if args.discovery_only:
                stats["discovery_only"] = True
            else:
                try:
                    browser.phase, browser.expected = "edit", browser.test_value
                    stats["stage"] = "edit"
                    run_phase(client, settings.agent_model, browser, stats)
                    stats["edit_verified"] = True
                finally:
                    browser.phase, browser.expected = "restore", str(browser.original)
                    stats["stage"] = "restore"
                    session.page.reload(wait_until="domcontentloaded")
                    run_phase(client, settings.agent_model, browser, stats)
                    stats["restoration_verified"] = True
    except Exception as error:
        stats["error_type"] = type(error).__name__
        if args.diagnostic:
            message = str(error).splitlines()[0]
            for secret in (email, password, settings.openai_api_key.get_secret_value()):
                message = message.replace(secret, "<redacted>")
            print(json.dumps({"error": message}), flush=True)
    finally:
        client.close()
    stats["elapsed_seconds"] = round(time.monotonic() - started, 1)
    print(json.dumps(stats, indent=2), flush=True)
    if args.discovery_only:
        return 0 if stats.get("discovery_verified") else 1
    return 0 if stats["edit_verified"] and stats["restoration_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
