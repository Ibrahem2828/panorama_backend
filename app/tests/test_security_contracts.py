from apps.accounts.serializers import StudentProfileSerializer
from apps.chat.serializers import MessageSerializer
from apps.feedback.serializers import PublicSuggestionSerializer
from apps.groups.serializers import GroupSerializer
from apps.printing.serializers import MobilePrintOrderSerializer
from apps.verification.serializers import VerificationRequestStudentSerializer


def test_sensitive_urls_and_internal_fields_are_not_in_mobile_contracts():
    assert "attachment" not in MessageSerializer.Meta.fields
    assert "internal_notes" not in MobilePrintOrderSerializer.Meta.fields
    assert "assigned_to" not in MobilePrintOrderSerializer.Meta.fields
    assert "pricing_snapshot" not in MobilePrintOrderSerializer.Meta.fields
    assert "card_image" not in StudentProfileSerializer.Meta.fields
    assert VerificationRequestStudentSerializer().fields["card_image"].write_only is True
    assert "whatsapp_url" not in GroupSerializer.Meta.fields


def test_public_suggestion_contract_is_anonymous_and_sanitized():
    fields = set(PublicSuggestionSerializer.Meta.fields)
    assert "user" not in fields
    assert "user_name" not in fields
    assert "email" not in fields
    assert "device_model" not in fields
    assert "metadata" not in fields
    assert "internal_notes" not in fields
    assert "assigned_to" not in fields
