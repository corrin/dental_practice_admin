"""Run one practice task by hand and look at what it produced.

    uv run python -m scripts.try_task ../admin_scripts/tasks/day_sheet date=2026-09-28
    uv run python -m scripts.try_task ../admin_scripts/tasks/day_sheet --environment staging --real

Loads and runs the task's `source.txt` with the application's own loader and `Services`,
against the fake Principle by default. Shared modules come from the practice repository's
`shared/` beside the task, where installation copies them from. The fake is one in-process
store seeded for 2026-09-28, the day the day sheet's scenarios sit on. Prints the summary and
coverage and saves the result. When the result carries a printable page, the one the run page
links to, it also prints it to an A4 PDF and opens it, the check a person does at the printer.

Staging and production are real Principles: staging holds a migrated copy of the practice's
patients. A task may write, so either needs `--real` as well.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from pydantic import SecretStr

from dental_practice_admin.config import (
    FAKE_API_KEY,
    FAKE_PRACTICE_ID,
    Environment,
    Settings,
)
from dental_practice_admin.scripts import Result, Services, load_source, run_loaded


def arguments() -> argparse.Namespace:
    """The task, its inputs as NAME=VALUE, and which Principle to run against."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("task", type=Path, help="a task directory holding source.txt")
    parser.add_argument("inputs", nargs="*", metavar="NAME=VALUE", help="task inputs")
    parser.add_argument("--environment", type=Environment, default=Environment.FAKE)
    parser.add_argument("--real", action="store_true",
                        help="confirm a run against staging or production")
    parser.add_argument("--no-open", action="store_true", help="write the PDF but don't open it")
    return parser.parse_args()


def services(environment: Environment) -> Services:
    """The application's integrations, with the fake in place of the sockets for FAKE."""
    if environment is Environment.FAKE:
        # Imported here so that a real run never loads test code.
        from tests.fake import seed, transport
        settings = Settings(environment=environment, api_key=SecretStr(FAKE_API_KEY),
                            practice_id=FAKE_PRACTICE_ID)
        return Services(settings, transport=transport(seed()))
    settings = Settings(environment=environment)
    settings.require_credentials()
    return Services(settings)


async def run(task: Path, inputs: dict[str, str], environment: Environment) -> Result:
    """The task's result, held to the application's result contract."""
    sys.path.insert(0, str(task.resolve().parents[1] / "shared"))
    source = task / "source.txt"
    namespace = load_source(source.read_text(encoding="utf-8"), str(source))
    integrations = services(environment)
    try:
        return await run_loaded(namespace, integrations, inputs)
    finally:
        await integrations.aclose()


def print_pdf(html: str, target: Path) -> int:
    """The page as an A4 PDF, by Chromium; returns the page count."""
    from playwright.sync_api import sync_playwright
    page_file = target.with_suffix(".html")
    page_file.write_text(html, encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(page_file.resolve().as_uri())
            page.pdf(path=str(target), prefer_css_page_size=True, print_background=True)
        finally:
            browser.close()
    return len(re.findall(rb"/Type\s*/Page[^s]", target.read_bytes()))


def main() -> None:
    """Run, report, save, and print when the task can."""
    args = arguments()
    if args.environment is not Environment.FAKE and not args.real:
        raise SystemExit(f"a task may write: add --real to run it against "
                         f"{args.environment.value}")
    malformed = [item for item in args.inputs if "=" not in item]
    if malformed:
        raise SystemExit(f"inputs are NAME=VALUE: {', '.join(malformed)}")
    inputs = dict(item.split("=", 1) for item in args.inputs)
    result = asyncio.run(run(args.task, inputs, args.environment))

    print(f"{result.coverage.value}: {result.summary}")
    # Real runs carry patient data, so their output stays in the environment's data directory.
    out = Settings(environment=args.environment).data_dir / "try"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    saved = out / f"{args.task.name}-{stamp}.json"
    saved.write_text(json.dumps(result.model_dump(mode="json", exclude={"printable"}),
                                indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"result: {saved}")
    if result.printable is None:
        return
    pdf = saved.with_suffix(".pdf")
    pages = print_pdf(result.printable, pdf)
    print(f"printed: {pdf} ({pages} pages)")
    if not args.no_open and hasattr(os, "startfile"):
        os.startfile(pdf)


if __name__ == "__main__":
    main()
