"""Tests for flight_blender.scd/utils.py – UAVSerialNumberValidator and OperatorRegistrationNumberValidator."""

import datetime
import json
import uuid
from enum import Enum

import pytest

from flight_blender.services.scd_svc import OperatorRegistrationNumberValidator, UAVSerialNumberValidator


# ---------------------------------------------------------------------------
# UAVSerialNumberValidator
# ---------------------------------------------------------------------------


class TestUAVSerialNumberValidator:
    def test_valid_serial_number(self):
        # ABCD1X is 4-char manufacturer code (no O/I), length-code "1" (1 char), then "X"
        # "ABCD" + "1" + "X" = 6 chars, length code "1" means 1 char body
        sn = "ABCD1X"
        assert UAVSerialNumberValidator(sn).is_valid() is True

    def test_empty_serial_too_short_manufacturer_code(self):
        assert UAVSerialNumberValidator("").is_valid() is False

    def test_manufacturer_code_with_O_invalid(self):
        # Contains 'O' in manufacturer code
        sn = "ABOD1X"
        assert UAVSerialNumberValidator(sn).is_valid() is False

    def test_manufacturer_code_with_I_invalid(self):
        sn = "ABID1X"
        assert UAVSerialNumberValidator(sn).is_valid() is False

    def test_invalid_length_code(self):
        # "G" is not a valid length code
        sn = "ABCDGX"
        assert UAVSerialNumberValidator(sn).is_valid() is False

    def test_body_length_mismatch(self):
        # Length code "2" means 2-char body, but only 1 char provided
        sn = "ABCD2X"
        assert UAVSerialNumberValidator(sn).is_valid() is False

    def test_body_length_correct_for_2(self):
        # Length code "2" means 2-char body
        sn = "ABCD2XY"
        assert UAVSerialNumberValidator(sn).is_valid() is True

    def test_code_contains_O_or_I_returns_true(self):
        v = UAVSerialNumberValidator("TEST")
        assert v.code_contains_O_or_I("ABOD") is True
        assert v.code_contains_O_or_I("ABID") is True

    def test_code_contains_O_or_I_returns_false(self):
        v = UAVSerialNumberValidator("TEST")
        assert v.code_contains_O_or_I("ABCD") is False


# ---------------------------------------------------------------------------
# OperatorRegistrationNumberValidator
# ---------------------------------------------------------------------------


class TestOperatorRegistrationNumberValidator:
    def _make_valid_oprn(self):
        """Generate a registration number that passes validation."""
        # Format: [3 country][12 alphanumeric (12 base digits)][1 checksum]-[3 secure]
        # oprn = country_code(3) + base_id(12) + checksum(1) = 16 chars
        # Then hyphen + 3 random alphanumeric
        # For EN4709-02: gen_checksum(base_id + random_three) where base_id is chars 3..14 of oprn
        validator = OperatorRegistrationNumberValidator("placeholder")
        country = "FIN"
        base = "abcdefghijk"  # 11 chars (base_id = oprn[3:-1] = chars 3 to 14 (index 3..14) = 12 chars)
        # oprn is 16 chars: country(3) + base_id(12) + checksum(1)
        # base_id = oprn[3:-1] = 12 chars
        base_id_part = "abcdefghijkl"  # exactly 12 chars for base_id portion
        random_3 = "abc"
        raw_id_for_checksum = base_id_part + random_3  # 15 chars
        checksum = validator.gen_checksum(raw_id_for_checksum)
        oprn = country + base_id_part + checksum  # 3 + 12 + 1 = 16 chars
        return oprn + "-" + random_3

    def test_valid_operator_registration_number(self):
        oprn = self._make_valid_oprn()
        validator = OperatorRegistrationNumberValidator(oprn)
        assert validator.is_valid() is True

    def test_missing_hyphen_returns_false(self):
        validator = OperatorRegistrationNumberValidator("NOTVALIDATALL")
        assert validator.is_valid() is False

    def test_short_oprn_part_returns_false(self):
        validator = OperatorRegistrationNumberValidator("SHORTONE-abc")
        assert validator.is_valid() is False

    def test_short_secure_chars_returns_false(self):
        validator = OperatorRegistrationNumberValidator("FINabcdefghijkla-ab")
        assert validator.is_valid() is False

    def test_non_alphanumeric_base_id_returns_false(self):
        # base_id must be alphanumeric; use hyphens in body
        validator = OperatorRegistrationNumberValidator("FIN----abcdefg-abc")
        assert validator.is_valid() is False

    def test_wrong_checksum_returns_false(self):
        valid = self._make_valid_oprn()
        # Corrupt the checksum (position -5 from the full string)
        chars = list(valid)
        # checksum is at position -5 (chars[-5])
        original = chars[-5]
        chars[-5] = "z" if original != "z" else "a"
        corrupted = "".join(chars)
        validator = OperatorRegistrationNumberValidator(corrupted)
        assert validator.is_valid() is False

    def test_gen_checksum_rejects_non_alnum(self):
        validator = OperatorRegistrationNumberValidator("placeholder")
        with pytest.raises(ValueError, match="alphanumeric"):
            validator.gen_checksum("abc-def-ghi-efg")  # contains hyphens

    def test_gen_checksum_rejects_wrong_length(self):
        validator = OperatorRegistrationNumberValidator("placeholder")
        with pytest.raises(ValueError, match="15 characters"):
            validator.gen_checksum("short")


# ---------------------------------------------------------------------------
# JSON codecs additional coverage
# ---------------------------------------------------------------------------


class TestJSONCodecsCoverage:
    """Additional tests for json_codecs."""

    def test_lazy_encoder_decimal(self):
        """Test LazyEncoder with Decimal."""
        from decimal import Decimal
        from flight_blender.utils.json_codecs import LazyEncoder

        result = json.dumps({"value": Decimal("3.14")}, cls=LazyEncoder)

        assert result == '{"value": 3.14}'

    def test_lazy_encoder_datetime(self):
        """Test LazyEncoder with datetime."""
        import datetime
        from flight_blender.utils.json_codecs import LazyEncoder

        now = datetime.datetime.now()
        result = json.dumps({"value": now}, cls=LazyEncoder)

        assert now.isoformat() in result

    def test_lazy_encoder_uuid(self):
        """Test LazyEncoder with UUID."""
        from flight_blender.utils.json_codecs import LazyEncoder

        test_uuid = uuid.uuid4()
        result = json.dumps({"value": test_uuid}, cls=LazyEncoder)

        assert str(test_uuid) in result

    def test_enhanced_json_encoder_datetime(self):
        """Test EnhancedJSONEncoder with datetime."""
        import datetime
        from flight_blender.utils.json_codecs import EnhancedJSONEncoder

        now = datetime.datetime.now()
        result = json.dumps({"value": now}, cls=EnhancedJSONEncoder)

        assert now.isoformat() in result

    def test_enhanced_json_encoder_dataclass(self):
        """Test EnhancedJSONEncoder with dataclass."""
        from dataclasses import dataclass
        from flight_blender.utils.json_codecs import EnhancedJSONEncoder

        @dataclass
        class TestData:
            name: str
            value: int

        data = TestData(name="test", value=42)
        result = json.dumps({"data": data}, cls=EnhancedJSONEncoder)

        assert '"name": "test"' in result
        assert '"value": 42' in result

    def test_enhanced_json_encoder_enum(self):
        """Test EnhancedJSONEncoder with enum."""
        from flight_blender.utils.json_codecs import EnhancedJSONEncoder

        class TestEnum(Enum):
            VALUE1 = "value1"
            VALUE2 = "value2"

        result = json.dumps({"value": TestEnum.VALUE1}, cls=EnhancedJSONEncoder)

        assert '"value": "value1"' in result

    def test_enhanced_json_encoder_decimal(self):
        """Test EnhancedJSONEncoder with Decimal."""
        from decimal import Decimal
        from flight_blender.utils.json_codecs import EnhancedJSONEncoder

        result = json.dumps({"value": Decimal("3.14")}, cls=EnhancedJSONEncoder)

        assert '{"value": 3.14}' in result

    def test_enhanced_json_encoder_uuid(self):
        """Test EnhancedJSONEncoder with UUID."""
        from flight_blender.utils.json_codecs import EnhancedJSONEncoder

        test_uuid = uuid.uuid4()
        result = json.dumps({"value": test_uuid}, cls=EnhancedJSONEncoder)

        assert str(test_uuid) in result

    def test_enhanced_json_decoder_datetime(self):
        """Test EnhancedJSONDecoder with datetime."""
        from flight_blender.utils.json_codecs import EnhancedJSONDecoder

        json_str = '{"value": "2024-01-01T00:00:00"}'
        result = json.loads(json_str, cls=EnhancedJSONDecoder)

        assert isinstance(result["value"], datetime.datetime)
# Altitude service additional coverage
# ---------------------------------------------------------------------------


class TestAltitudeServiceCoverage:
    """Additional tests for altitude service."""

    def test_wgs84_to_barometric(self):
        """Test wgs84_to_barometric function."""
        from flight_blender.services.altitude import wgs84_to_barometric

        msl_height, pressure_altitude = wgs84_to_barometric(
            lat=0.0,
            lon=0.0,
            hae_meters=100.0,
        )

        assert isinstance(msl_height, float)
        assert isinstance(pressure_altitude, float)
        assert msl_height == pressure_altitude

    def test_wgs84_to_barometric_negative_altitude(self):
        """Test wgs84_to_barometric with negative altitude."""
        from flight_blender.services.altitude import wgs84_to_barometric

        msl_height, pressure_altitude = wgs84_to_barometric(
            lat=0.0,
            lon=0.0,
            hae_meters=-100.0,
        )

        assert isinstance(msl_height, float)
        assert isinstance(pressure_altitude, float)
        assert msl_height == pressure_altitude

    def test_wgs84_to_barometric_high_altitude(self):
        """Test wgs84_to_barometric with high altitude."""
        from flight_blender.services.altitude import wgs84_to_barometric

        msl_height, pressure_altitude = wgs84_to_barometric(
            lat=0.0,
            lon=0.0,
            hae_meters=10000.0,
        )

        assert isinstance(msl_height, float)
        assert isinstance(pressure_altitude, float)
        assert msl_height == pressure_altitude
