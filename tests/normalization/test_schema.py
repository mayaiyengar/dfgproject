from datetime import date

import pytest
from pydantic import ValidationError

from models.enums import Level, NormalizedStatus, PolicyType
from normalization.schema import PolicyIn, RawRecord


def _valid_kwargs(**overrides):
    kwargs = dict(
        source="test_source",
        external_id="HB-1",
        title="A Test Bill",
        policy_type=PolicyType.BILL,
        jurisdiction="california",
        state="CA",
        level=Level.STATE,
        source_url="https://example.gov/bill/1",
    )
    kwargs.update(overrides)
    return kwargs


def test_minimal_valid_policy_in():
    policy = PolicyIn(**_valid_kwargs())
    assert policy.title == "A Test Bill"
    assert policy.normalized_status == NormalizedStatus.UNKNOWN
    assert policy.raw_payload == {}


def test_blank_title_rejected():
    with pytest.raises(ValidationError, match="title must not be blank"):
        PolicyIn(**_valid_kwargs(title="   "))


def test_missing_source_url_rejected():
    with pytest.raises(ValidationError):
        PolicyIn(**_valid_kwargs(source_url=""))


def test_title_is_stripped():
    policy = PolicyIn(**_valid_kwargs(title="  Padded Title  "))
    assert policy.title == "Padded Title"


def test_raw_payload_must_be_json_serializable():
    class NotSerializable:
        pass

    with pytest.raises(ValidationError, match="JSON-serializable"):
        PolicyIn(**_valid_kwargs(raw_payload={"bad": NotSerializable()}))


def test_dates_parsed_from_iso_strings():
    policy = PolicyIn(**_valid_kwargs(introduction_date="2026-01-15"))
    assert policy.introduction_date == date(2026, 1, 15)


def test_full_policy_round_trips_all_fields():
    policy = PolicyIn(
        source="ca_regulations",
        external_id="OAL-2026-0042",
        title="Proposed Rule on School Hygiene Product Access",
        short_title="School Hygiene Rule",
        bill_number=None,
        policy_type=PolicyType.REGULATION,
        jurisdiction="california",
        state="CA",
        level=Level.STATE,
        chamber=None,
        session=None,
        description="A proposed regulation requiring free menstrual products in public schools.",
        official_status="Proposed",
        normalized_status=NormalizedStatus.PROPOSED,
        introduction_date=date(2026, 1, 1),
        last_action_date=date(2026, 2, 1),
        source_url="https://oal.ca.gov/notice/42",
        official_text_url="https://oal.ca.gov/notice/42.pdf",
        raw_payload={"issue": "2026-05", "notice_number": "42"},
    )
    assert policy.policy_type == PolicyType.REGULATION
    assert policy.official_text_url == "https://oal.ca.gov/notice/42.pdf"


def test_raw_record_requires_data_url_and_fetched_at():
    record = RawRecord(data={"foo": "bar"}, source_url="https://example.gov", fetched_at="2026-01-01T00:00:00Z")
    assert record.data == {"foo": "bar"}
    assert record.raw_document_bytes is None
