"""The fake Principle: its own data state, served in its own process."""

from tests.fake.server import app, routes_served
from tests.fake.store import FAKE_API_KEY, FAKE_PRACTICE_ID

__all__ = [
    "FAKE_API_KEY",
    "FAKE_PRACTICE_ID",
    "app",
    "routes_served",
]
