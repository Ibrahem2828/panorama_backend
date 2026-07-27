from apps.printing.serializers import MobilePrintOrderStatusHistorySerializer, PrintStatusUpdateSerializer


def test_print_status_contract_separates_public_and_internal_notes():
    assert "public_note" in MobilePrintOrderStatusHistorySerializer.Meta.fields
    assert "internal_note" not in MobilePrintOrderStatusHistorySerializer.Meta.fields
    serializer = PrintStatusUpdateSerializer(
        data={
            "status": "under_review",
            "public_note": "طلبك قيد المراجعة.",
            "internal_note": "Check page count manually.",
        }
    )
    assert serializer.is_valid(), serializer.errors
