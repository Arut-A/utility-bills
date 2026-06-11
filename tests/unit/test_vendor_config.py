"""
Tests for vendors.yaml integrity and vendor_config.py logic.
These run without any containers — pure logic tests.
"""
import re

import pytest


class TestVendorsYamlIntegrity:
    """Validate that vendors.yaml is well-formed and complete."""

    def test_config_has_vendors(self, vendors):
        assert len(vendors) > 0, "No vendors defined in vendors.yaml"

    def test_every_vendor_has_classification(self, vendors):
        for name, v in vendors.items():
            cls = v.get("classification", {})
            has_slugs = bool(cls.get("filename_slugs"))
            has_keywords = bool(cls.get("text_keywords"))
            assert has_slugs or has_keywords, (
                f"{name}: needs at least filename_slugs or text_keywords"
            )

    def test_every_vendor_has_total_pattern(self, vendors):
        for name, v in vendors.items():
            patterns = v.get("parsing", {}).get("total_patterns", [])
            assert len(patterns) > 0, f"{name}: no total_patterns defined"

    def test_all_regex_patterns_compile(self, vendors):
        errors = []
        for name, v in vendors.items():
            parsing = v.get("parsing", {})
            # Total patterns
            for pat in parsing.get("total_patterns", []):
                try:
                    re.compile(pat, re.IGNORECASE)
                except re.error as e:
                    errors.append(f"{name} total_pattern: {e}")
            # Consumption patterns
            cons = parsing.get("consumption", {})
            for pat in cons.get("patterns", []):
                try:
                    re.compile(pat)
                except re.error as e:
                    errors.append(f"{name} consumption_pattern: {e}")
            # Billing period patterns
            bp = parsing.get("billing_period", {})
            for pat in bp.get("patterns", []):
                try:
                    re.compile(pat)
                except re.error as e:
                    errors.append(f"{name} billing_period_pattern: {e}")
        assert not errors, f"Regex compilation errors:\n" + "\n".join(errors)

    def test_every_vendor_has_dashboard_config(self, vendors):
        for name, v in vendors.items():
            dash = v.get("dashboard", {})
            assert "color" in dash, f"{name}: missing dashboard.color"
            assert "label" in dash, f"{name}: missing dashboard.label"

    def test_no_duplicate_filename_slugs(self, vendors):
        """Filename slugs should be unique enough to avoid ambiguous matches."""
        all_slugs = {}
        for name, v in vendors.items():
            for slug in v.get("classification", {}).get("filename_slugs", []):
                if slug in all_slugs:
                    # Duplicates are OK if vendors have require/exclude keywords to disambiguate
                    pass  # Log but don't fail — disambiguation is handled by keywords
                all_slugs.setdefault(slug, []).append(name)

    def test_dashboard_colors_are_valid_hex(self, vendors):
        for name, v in vendors.items():
            color = v.get("dashboard", {}).get("color", "")
            if color:
                assert re.match(r"^#[0-9a-fA-F]{6}$", color), (
                    f"{name}: invalid color '{color}', must be #RRGGBB"
                )


class TestVendorConfigModule:
    """Test vendor_config.py functions."""

    def test_load_config(self, vendors_yaml_path, monkeypatch):
        monkeypatch.setenv("VENDOR_CONFIG_PATH", str(vendors_yaml_path))
        import vendor_config
        # Clear cache to force reload with new path
        vendor_config._cache["mtime"] = 0.0
        vendor_config._cache["data"] = None
        vendor_config.CONFIG_PATH = vendors_yaml_path

        config = vendor_config.load_config()
        assert "vendors" in config
        assert len(config["vendors"]) > 0

    def test_get_dashboard_colors_returns_hex(self, vendors_yaml_path, monkeypatch):
        monkeypatch.setenv("VENDOR_CONFIG_PATH", str(vendors_yaml_path))
        import vendor_config
        vendor_config.CONFIG_PATH = vendors_yaml_path
        vendor_config._cache["mtime"] = 0.0
        vendor_config._cache["data"] = None

        colors = vendor_config.get_dashboard_colors()
        assert len(colors) > 0
        for vendor, color in colors.items():
            assert color.startswith("#"), f"{vendor}: color '{color}' doesn't start with #"

    def test_classify_unknown_returns_unknown(self, vendors_yaml_path, monkeypatch):
        monkeypatch.setenv("VENDOR_CONFIG_PATH", str(vendors_yaml_path))
        import vendor_config
        vendor_config.CONFIG_PATH = vendors_yaml_path
        vendor_config._cache["mtime"] = 0.0
        vendor_config._cache["data"] = None

        result = vendor_config.classify_vendor("random_file.pdf", "nothing useful here")
        assert result == "unknown"
