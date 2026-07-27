from rest_framework import serializers

from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.full_name", read_only=True)

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "actor",
            "actor_name",
            "action",
            "target_type",
            "target_id",
            "old_value",
            "new_value",
            "ip_address",
            "user_agent",
            "created_at",
        ]
        read_only_fields = fields
