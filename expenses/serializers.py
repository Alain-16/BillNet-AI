import zoneinfo 

from rest_framework import serializers

from expenses.models import Project


class ProjectSerializer(serializers.ModelSerializer):

    qbo_customer_name = serializers.CharField(source="qbo_customer.name", read_only=True, default=None)
    qbo_customer_external_id = serializers.CharField(source="qbo_customer.external_id", read_only=True, default=None)
    aliases = serializers.ListField(child=serializers.CharField(max_length=255), required=False, allow_empty=True)


    class Meta:
        model = Project
        fields =[
            "id", "code", "name", "aliases", "status",
            "active_from", "active_to",
            "qbo_customer", "qbo_customer_name", "qbo_customer_external_id",
            "version", "created_at", "updated_at",
        ]

        read_only_fields = [
            "id", "qbo_customer", "qbo_customer_name", "qbo_customer_external_id",
            "version", "created_at", "updated_at",
        ]



class ProjectLinkSerializer(serializers.Serializer):
    reference_id = serializers.UUIDField()

class ProjectClosesSerializer(serializers.Serializer):
    closed_on = serializers.DateField(required=False)
    