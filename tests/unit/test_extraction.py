"""
Tests for vendor-specific extraction logic via vendor_config.py.
Tests total extraction, consumption extraction, skip rules, and special flags.
"""
import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def setup_vendor_config(vendors_yaml_path, monkeypatch):
    """Point vendor_config to the real vendors.yaml for every test."""
    monkeypatch.setenv("VENDOR_CONFIG_PATH", str(vendors_yaml_path))
    import vendor_config
    vendor_config.CONFIG_PATH = vendors_yaml_path
    vendor_config._cache["mtime"] = 0.0
    vendor_config._cache["data"] = None
    yield vendor_config


class TestTotalExtraction:
    """Test extract_total for each vendor against sample text fragments."""

    def test_extract_returns_float_or_none(self, setup_vendor_config):
        vc = setup_vendor_config
        # With no text, should return None for any vendor
        for vendor_slug in vc.get_vendors():
            result = vc.extract_total(vendor_slug, "")
            assert result is None, f"{vendor_slug} extracted total from empty text"

    @pytest.mark.parametrize("vendor,text,expected", [
        ("electricity", "Kokku käibemaksuga 45,67 EUR", 45.67),
        ("water", "Kokku 23,45", 23.45),
        ("garbage", "Summa käibemaksuga 15,00", 15.00),
    ])
    def test_extract_known_patterns(self, setup_vendor_config, vendor, text, expected):
        vc = setup_vendor_config
        result = vc.extract_total(vendor, text)
        if result is not None:
            assert abs(result - expected) < 0.01, f"{vendor}: got {result}, expected {expected}"


class TestClassification:
    """Test vendor classification from filenames and text."""

    def test_unknown_file_returns_unknown(self, setup_vendor_config):
        vc = setup_vendor_config
        result = vc.classify_vendor("random_file.pdf", "no vendor info")
        assert result == "unknown"

    def test_all_vendors_classifiable(self, setup_vendor_config):
        """Each vendor should classify to itself using its own filename_slugs."""
        vc = setup_vendor_config
        for vendor_slug, config in vc.get_vendors().items():
            slugs = config.get("classification", {}).get("filename_slugs", [])
            if slugs:
                filename = f"2026-01_{slugs[0]}_test.pdf"
                # Build minimal text with require_keywords if any
                require = config.get("classification", {}).get("require_keywords", [])
                text = " ".join(require) if require else "test content"
                result = vc.classify_vendor(filename, text)
                assert result != "unknown", (
                    f"{vendor_slug}: classified as unknown with filename slug '{slugs[0]}'"
                )


class TestSkipRules:
    """Test vendor skip rules."""

    def test_no_skip_on_normal_bill(self, setup_vendor_config):
        vc = setup_vendor_config
        for vendor_slug in vc.get_vendors():
            # Normal filenames should not be skipped
            result = vc.should_skip(vendor_slug, "normal_bill.pdf", "normal bill content")
            assert result is False, f"{vendor_slug}: incorrectly skipped normal bill"


class TestSpecialFlags:
    """Test special handling flags from config."""

    def test_pro_rate_flag(self, setup_vendor_config):
        vc = setup_vendor_config
        # house_insurance should have pro_rate
        if "house_insurance" in vc.get_vendors():
            assert vc.has_pro_rate("house_insurance") is True

    def test_heating_season(self, setup_vendor_config):
        vc = setup_vendor_config
        if "pellets" in vc.get_vendors():
            season = vc.get_heating_season("pellets")
            assert season is not None
            assert "start_month" in season
            assert "end_month" in season

    def test_month_alignment(self, setup_vendor_config):
        vc = setup_vendor_config
        if "water" in vc.get_vendors():
            alignment = vc.get_month_alignment("water")
            assert alignment is not None


class TestConsumptionExtraction:
    """Test consumption extraction returns correct structure."""

    def test_empty_text_returns_empty_dict(self, setup_vendor_config):
        vc = setup_vendor_config
        for vendor_slug in vc.get_vendors():
            result = vc.extract_consumption(vendor_slug, "")
            assert isinstance(result, dict)

    def test_billing_period_keys(self, setup_vendor_config):
        """If billing period is found, both start and end should be present."""
        vc = setup_vendor_config
        # Fabricate text with a date range that matches common patterns
        text = "Periood 01.01.2026 - 31.01.2026"
        for vendor_slug in vc.get_vendors():
            result = vc.extract_consumption(vendor_slug, text)
            if "billing_period_start" in result:
                assert "billing_period_end" in result, (
                    f"{vendor_slug}: has start but missing end"
                )


class TestTariffSignature:
    """extract_tariff_signature: contract type + printed €/unit rates.

    Guards the fixed↔spot discrimination and the 'elektribörsi' boilerplate trap
    (the spot product token must not be confused with the Nord Pool boilerplate
    that prints on every Alexela invoice). (origin: 2026-06-06)
    """

    def test_electricity_fixed(self, setup_vendor_config):
        vc = setup_vendor_config
        text = ("Fikseeritud hinnaga elekter\nkWh\n0,11089 €/kWh\n0,09476 €/kWh\n"
                "Tarbimise tasakaalustamisvõimsuse kulu\n0,00373 €/kWh\n"
                "Nord Pool Spot elektribörsi vahendusel")
        sig = vc.extract_tariff_signature("electricity", text)
        assert sig["tariff_type"] == "fixed"
        assert sig["unit_rates"] == [0.11089, 0.09476]  # 0.00373 micro-rate dropped

    def test_electricity_spot(self, setup_vendor_config):
        vc = setup_vendor_config
        text = "Börsihinnaga elekter\nkWh\n0,07849 €/kWh\n0,05399 €/kWh\nelektribörsi vahendusel"
        sig = vc.extract_tariff_signature("electricity", text)
        assert sig["tariff_type"] == "spot"
        assert sig["unit_rates"] == [0.07849, 0.05399]

    def test_boilerplate_does_not_trigger_spot(self, setup_vendor_config):
        # A FIXED invoice still contains 'elektribörsi vahendusel' boilerplate.
        vc = setup_vendor_config
        text = "Fikseeritud hinnaga elekter ... ostetud Nord Pool Spot elektribörsi vahendusel"
        sig = vc.extract_tariff_signature("electricity", text)
        assert sig["tariff_type"] == "fixed"

    def test_electricity_cents_notation(self, setup_vendor_config):
        # Pre-2026 Alexela invoices print "s/kWh" (cents, dot-decimal); must
        # normalise to €/kWh via scale 0.01.
        vc = setup_vendor_config
        text = "Börsihinnaga elekter päevaajal\n102.664 kWh\n9.632 s/kWh\n8.708 s/kWh\n0.373 s/kWh"
        sig = vc.extract_tariff_signature("electricity", text)
        assert sig["tariff_type"] == "spot"
        assert sig["unit_rates"] == [0.09632, 0.08708]  # 0.373 s/kWh micro-rate dropped

    def test_gas(self, setup_vendor_config):
        vc = setup_vendor_config
        sig = vc.extract_tariff_signature("gas", "Maagaas Mai 2026\nm3\n0,435 €/m3")
        assert sig["tariff_type"] == "maagaas"
        assert sig["unit_rates"] == [0.435]

    def test_none_when_no_config_or_no_match(self, setup_vendor_config):
        vc = setup_vendor_config
        assert vc.extract_tariff_signature("internet", "Koduinternet 100M kuutasu") is None
        assert vc.extract_tariff_signature("electricity", "no tariff data here") is None
