"""The diary operation: the numbers staff act on, and honesty about coverage."""

from __future__ import annotations

from datetime import date

from principle_admin.principle import PrincipleClient
from principle_admin.storage import Coverage
from principle_admin.tasks import daily_diary, local_day_window
from tests.fake import FAKE_PRACTICE_ID, FakeStore

SEEDED_DAY = date(2026, 9, 28)
SLOTS_PER_DAY = 8


async def test_reports_one_local_day(fake_client: PrincipleClient) -> None:
    """The day must be a Pacific/Auckland day, not a UTC one.

    The seed runs 09:00-13:00 NZ, which is the previous UTC day for most of the year. A
    refactor to UTC day boundaries would report a nearly empty morning and split the diary
    across two reports.
    """
    report = await daily_diary(fake_client, SEEDED_DAY)
    assert report.appointments == SLOTS_PER_DAY
    assert report.on_date == SEEDED_DAY


async def test_local_day_window_is_sent_as_utc() -> None:
    """The wire form must be unambiguous.

    Sending "+13:00" would be correct but leaves the server to interpret the offset; a
    server or a fake comparing timestamps as text then answers with the wrong day.
    """
    window = local_day_window(SEEDED_DAY)
    assert window == {"from": "2026-09-27T11:00:00Z", "to": "2026-09-28T11:00:00Z"}


async def test_cancelled_appointments_are_counted_but_not_attending(
    fake_client: PrincipleClient,
) -> None:
    """Attending must exclude cancellations while the booking stays visible.

    A count that treated cancelled as attending would have staff prepare for patients who
    are not coming; one that dropped them entirely would hide the day's churn.
    """
    report = await daily_diary(fake_client, SEEDED_DAY)
    assert report.by_status["cancelled"] > 0
    assert report.attending == report.appointments - report.by_status["cancelled"]
    assert sum(report.by_status.values()) == report.appointments


async def test_grouped_by_practitioner_with_names_and_bounds(
    fake_client: PrincipleClient,
) -> None:
    """A regression in the grouping or the name lookup shows up as an unnamed column."""
    report = await daily_diary(fake_client, SEEDED_DAY)
    assert len(report.by_practitioner) == 2
    assert all(day.name.startswith("Dr ") for day in report.by_practitioner)
    assert sum(day.appointments for day in report.by_practitioner) == report.appointments
    first = report.by_practitioner[0]
    assert first.first_from is not None and first.last_to is not None
    assert first.first_from < first.last_to


async def test_times_are_shown_in_practice_local_time(fake_client: PrincipleClient) -> None:
    """Staff read a wall clock, not UTC.

    Principle sends UTC, and the seeded diary runs 09:00-13:00 NZ. Rendered straight from the
    wire it reads as 20:00-00:00 -- the previous evening -- which is what a dentist rings about.
    """
    report = await daily_diary(fake_client, SEEDED_DAY)
    assert [day.first_from for day in report.by_practitioner] == ["09:00", "09:30"]
    assert all(day.last_to is not None and day.last_to <= "13:00" for day in report.by_practitioner)


async def test_complete_day_reports_complete_coverage(fake_client: PrincipleClient) -> None:
    """The happy path must not be permanently labelled partial.

    A coverage flag that is always PARTIAL is as useless as one that is always COMPLETE --
    staff stop reading it either way.
    """
    report = await daily_diary(fake_client, SEEDED_DAY)
    assert report.coverage is Coverage.COMPLETE
    assert report.coverage_note is None
    assert "PARTIAL" not in report.summary()


async def test_an_appointment_for_an_unlisted_practitioner_is_partial(
    fake_client: PrincipleClient, fake_store: FakeStore
) -> None:
    """A practitioner the practice list does not name makes the report partial.

    Without this the page would show a column headed "unknown practitioner abc123" while
    the summary claimed a complete day, and the run would be filed as trustworthy.
    """
    fake_store.add_patient(
        "fake-patient-900", FAKE_PRACTICE_ID, "Patient 900", "2026-09-28T00:00:00Z"
    )
    fake_store.add_practitioner(
        "locum-not-in-list", FAKE_PRACTICE_ID, "Dr Locum", "2026-09-28T00:00:00Z"
    )
    fake_store.add_appointment(
        ident="fake-appointment-900",
        practice_id=FAKE_PRACTICE_ID,
        practitioner_id="locum-not-in-list",
        patient_id="fake-patient-900",
        event_from="2026-09-27T21:00:00Z",
        event_to="2026-09-27T21:30:00Z",
        status="scheduled",
        at="2026-09-28T00:00:00Z",
    )
    # Remove the locum from the practitioner listing while the appointment stays, which is
    # what a mid-day staffing change looks like through the API.
    fake_store.db.execute("DELETE FROM practitioners WHERE id = 'locum-not-in-list'")
    fake_store.db.commit()

    report = await daily_diary(fake_client, SEEDED_DAY)
    assert report.coverage is Coverage.PARTIAL
    assert report.coverage_note is not None
    assert "PARTIAL" in report.summary()


async def test_empty_day_is_complete_not_partial(fake_client: PrincipleClient) -> None:
    """A genuinely empty day must read as empty, not as a failure to look.

    Conflating the two is how "no appointments" becomes indistinguishable from "could not
    read the diary".
    """
    report = await daily_diary(fake_client, date(2026, 12, 25))
    assert report.appointments == 0
    assert report.coverage is Coverage.COMPLETE
