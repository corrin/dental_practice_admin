"""The fake serves every operation in the released interface (ADR 0007)."""

from __future__ import annotations

import re

import pytest

from dental_practice_admin.principle import CATALOGUE, Call
from tests.fake import FakeStore, dispatch
from tests.fake.server import ROUTES, FakeUnhandledRouteError, Request
from tests.fake.store import FAKE_API_KEY

SAMPLE_SEGMENT = "sample"

# Operations the fake has no route for yet. Each comes off this list in the change that records
# it and routes it; strict, so a routed operation still listed fails until it is taken off.
NOT_YET_ROUTED = frozenset({
    "cancelAppointment",
    "createAppointment",
    "createAppointmentInteraction",
    "createAppointmentTag",
    "createCallEvent",
    "createDeposit",
    "createEmail",
    "createLead",
    "createPatientInteraction",
    "createPatientTag",
    "createSMSMessage",
    "createTransaction",
    "getAccountCredit",
    "getAppointment",
    "getAppointmentTag",
    "getEmail",
    "getInvoice",
    "getPatientFile",
    "getPatientTag",
    "getPatientTreatmentStep",
    "getPractice",
    "getPractitioner",
    "getSMSMessage",
    "getTransaction",
    "getTreatmentOption",
    "listAccountCredits",
    "listAllowedAppointmentStatuses",
    "listAppointmentTags",
    "listAppointments",
    "listEmails",
    "listFilesByDateRange",
    "listInvoices",
    "listPatientFiles",
    "listPatientTags",
    "listPatientTreatmentPlans",
    "listSMSMessages",
    "listTransactions",
    "listTreatmentOptions",
    "listTreatmentPlansByDateRange",
    "rescheduleAppointment",
    "searchAvailability",
    "searchLeads",
    "searchRescheduleAvailability",
    "updateAppointmentStatus",
    "updatePatientFile",
    "updateSMSMessageStatus",
    "updateTransaction",
    "uploadPatientFile",
})


def _concrete_path(call: Call) -> str:
    """The call's path with every template segment filled, so a route regex can match it."""
    return re.sub(r"\{[^}]+\}", SAMPLE_SEGMENT, call.path)


@pytest.mark.parametrize("call", [
    pytest.param(call, marks=pytest.mark.xfail(strict=True, reason="not yet recorded and routed"))
    if call.name in NOT_YET_ROUTED else call for call in CATALOGUE], ids=lambda call: call.name)
def test_every_catalogue_call_is_routed(call: Call) -> None:
    """Every operation in the released interface reaches a fake route (ADR 0007)."""
    path = _concrete_path(call)
    matched = [
        pattern.pattern
        for method, pattern, _ in ROUTES
        if method == call.method and pattern.match(path)
    ]
    assert matched, (
        f"{call.name} ({call.method} {call.path}) has no route in tests/fake/server.py"
    )


def test_unrouted_call_refuses(fake_store: FakeStore) -> None:
    """A future edit that makes the fake answer an unknown path would be caught here.

    Guessing an answer is the failure this fake exists to avoid: it would return a shape
    nobody verified and the suite would go green on it.
    """
    request = Request(
        method="GET",
        path="/v1/not-in-the-interface",
        query={},
        headers={"x-api-key": FAKE_API_KEY},
    )
    with pytest.raises(FakeUnhandledRouteError, match="no route for"):
        dispatch(fake_store, request)
