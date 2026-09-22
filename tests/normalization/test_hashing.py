from datetime import date

from models.enums import Level, NormalizedStatus, PolicyType
from normalization.hashing import compute_content_hash
from normalization.schema import PolicyIn


def _policy(**overrides) -> PolicyIn:
    kwargs = dict(
        source="test_source",
        external_id="HB-1",
        title="A Test Bill",
        policy_type=PolicyType.BILL,
        jurisdiction="washington",
        state="WA",
        level=Level.STATE,
        source_url="https://example.gov/bill/1",
        official_status="Introduced",
        normalized_status=NormalizedStatus.INTRODUCED,
        last_action_date=date(2026, 1, 1),
        description="Original description.",
    )
    kwargs.update(overrides)
    return PolicyIn(**kwargs)


def test_identical_policies_produce_identical_hash():
    a = _policy()
    b = _policy()
    assert compute_content_hash(a) == compute_content_hash(b)


def test_status_change_produces_different_hash():
    a = _policy(official_status="Introduced", normalized_status=NormalizedStatus.INTRODUCED)
    b = _policy(official_status="Enacted", normalized_status=NormalizedStatus.ENACTED)
    assert compute_content_hash(a) != compute_content_hash(b)


def test_date_change_produces_different_hash():
    a = _policy(last_action_date=date(2026, 1, 1))
    b = _policy(last_action_date=date(2026, 2, 1))
    assert compute_content_hash(a) != compute_content_hash(b)


def test_description_change_produces_different_hash():
    a = _policy(description="Original description.")
    b = _policy(description="Substantively edited description.")
    assert compute_content_hash(a) != compute_content_hash(b)


def test_raw_payload_change_alone_does_not_change_hash():
    """raw_payload is explicitly excluded from the hash (hashing.py docstring)
    — a source re-serving the same substantive content with reordered/extra
    raw fields must not look like a policy change."""
    a = _policy(raw_payload={"a": 1})
    b = _policy(raw_payload={"a": 1, "extra_noise_field": "2026-09-22T00:00:00Z"})
    assert compute_content_hash(a) == compute_content_hash(b)


def test_hash_is_deterministic_across_calls():
    a = _policy()
    assert compute_content_hash(a) == compute_content_hash(a)


def test_hash_is_a_hex_sha256():
    h = compute_content_hash(_policy())
    assert len(h) == 64
    int(h, 16)  # raises ValueError if not valid hex
