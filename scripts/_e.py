from pathlib import Path
p = Path("src/dental_practice_admin/principle.py")
s = p.read_text(encoding="utf-8")
def rep(old, new):
    global s
    assert s.count(old) == 1, old[:70]
    s = s.replace(old, new)
rep("import hashlib\n", "import asyncio\nimport hashlib\n")
rep("from dataclasses import dataclass\n", "from dataclasses import dataclass\nfrom datetime import UTC, datetime, timedelta\n")
rep('''                    await self._in_practice(values["patientId"], cause.body.get("name"))''',
    '''                    await self._in_practice(values["patientId"], cause.body)''')
rep('''            await self._in_practice(values["patientId"], output["name"])''',
    '''            await self._in_practice(values["patientId"], output)''')
old_start = s.index("    async def _in_practice(self, patient_id: str, name: object) -> None:")
old_end = s.index("    async def get(self, name: str, *,")
s = s[:old_start] + '''    async def _in_practice(self, patient_id: str, patient: dict[str, Any]) -> None:
        """Refuse a patient the practice-scoped search by their name does not return.

        Principle's search finds a new patient only some seconds after it is created, so for a
        patient younger than `SEARCH_LAG` the search is asked again until it is that old.
        """
        name = patient.get("name")
        if not isinstance(name, str):
            raise CallError("Patient is outside this practice or search is inconclusive")
        created = patient.get("createdAt")
        searchable = (datetime.fromisoformat(created) + SEARCH_LAG if isinstance(created, str)
                      else datetime.now(UTC))
        while not await self._found(patient_id, name):
            if datetime.now(UTC) >= searchable:
                raise CallError("Patient is outside this practice or search is inconclusive")
            await asyncio.sleep(SEARCH_POLL.total_seconds())

    async def _found(self, patient_id: str, name: str) -> bool:
        """Whether the practice-scoped search by this name returns this patient."""
        try:
            rows = (await self.call("searchPatients", {"name": name}))["data"]
        except RecordError as error:
            rows = error.body["data"]
        return any(row["id"] == patient_id for row in rows)

''' + s[old_end:]
rep('''ACCEPTED_WEB_BUILDS''', '''# How long after createPatient Principle's name search may still miss the new patient. The
# staging write run measures it, and the integration tier fails if Principle takes longer.
SEARCH_LAG = timedelta(seconds=5)
SEARCH_POLL = timedelta(milliseconds=500)


ACCEPTED_WEB_BUILDS''')
p.write_text(s, encoding="utf-8")
