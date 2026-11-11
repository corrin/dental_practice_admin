"""The fake SMS bridge, over the real transport."""

from typing import Any

from pydantic import SecretStr

from tests.fake_sms_bridge.server import (
    FAKE_SMS_BRIDGE_API_KEY,
    FakeSmsBridge,
    app,
    failure,
    seed,
    transport,
)

# The key for Settings(...) and model_copy. The address is each test's: the fake has none of its
# own until it is served.
FAKE_SMS_BRIDGE_SETTINGS: dict[str, Any] = {
    "sms_bridge_api_key": SecretStr(FAKE_SMS_BRIDGE_API_KEY)}

__all__ = ["FAKE_SMS_BRIDGE_API_KEY", "FAKE_SMS_BRIDGE_SETTINGS", "FakeSmsBridge", "app",
           "failure", "seed", "transport"]
