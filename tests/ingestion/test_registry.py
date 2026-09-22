import pytest
import yaml

from ingestion.registry import SourceConfig, load_source_registry
from models.enums import Level, PolicyType, SourceType


_SAMPLE_CONFIG = {
    "sources": [
        {
            "source": "federal_register_rules",
            "jurisdiction": "federal",
            "level": "federal",
            "policy_types_covered": ["regulation", "executive_order"],
            "source_type": "api",
            "base_url": "https://www.federalregister.gov",
            "api_url": "https://www.federalregister.gov/api/v1",
            "auth_required": False,
            "enabled": True,
            "check_frequency": "daily",
        },
        {
            "source": "ca_regulations",
            "jurisdiction": "california",
            "state": "CA",
            "level": "state",
            "policy_types_covered": ["regulation"],
            "source_type": "pdf",
            "base_url": "https://oal.ca.gov",
            "enabled": False,
            "notes": "Disabled until Milestone: California adapters",
        },
    ]
}


def test_load_source_registry_parses_valid_config(tmp_path):
    config_path = tmp_path / "sources.yaml"
    config_path.write_text(yaml.dump(_SAMPLE_CONFIG))

    sources = load_source_registry(config_path)

    assert len(sources) == 2
    assert all(isinstance(s, SourceConfig) for s in sources)
    fed = next(s for s in sources if s.source == "federal_register_rules")
    assert fed.level == Level.FEDERAL
    assert fed.source_type == SourceType.API
    assert PolicyType.EXECUTIVE_ORDER in fed.policy_types_covered
    ca = next(s for s in sources if s.source == "ca_regulations")
    assert ca.enabled is False
    assert ca.state == "CA"


def test_load_source_registry_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_source_registry(tmp_path / "does-not-exist.yaml")


def test_load_source_registry_empty_sources_list(tmp_path):
    config_path = tmp_path / "sources.yaml"
    config_path.write_text(yaml.dump({"sources": []}))
    assert load_source_registry(config_path) == []


def test_load_source_registry_rejects_invalid_policy_type(tmp_path):
    bad_config = {
        "sources": [
            {
                "source": "bad_source",
                "jurisdiction": "federal",
                "level": "federal",
                "policy_types_covered": ["not_a_real_type"],
                "source_type": "api",
                "base_url": "https://example.gov",
            }
        ]
    }
    config_path = tmp_path / "sources.yaml"
    config_path.write_text(yaml.dump(bad_config))
    with pytest.raises(Exception):  # pydantic ValidationError
        load_source_registry(config_path)


def test_repo_sources_yaml_loads_and_is_currently_empty():
    """The project-foundation milestone ships config/sources.yaml with no
    entries — adapters are added to it one milestone at a time."""
    sources = load_source_registry("config/sources.yaml")
    assert sources == []
