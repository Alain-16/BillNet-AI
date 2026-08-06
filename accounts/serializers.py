import zoneinfo

from rest_framework import serializers

from accounts.models import Company, IntegrationConnection, User
from common.enums import ConnectionStatus

class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ["id","name","currency","timezone","status",
                  "auto_post_enabled","tax_codes","created_at","updated_at"]
        read_only_fields = ["id","status","tax_codes","created_at","updated_at"]


    def validate_currency(self,value):
        value = value.upper()
        if len(value) != 3 or not value.isalpha():
            raise serializers.ValidationError("Currency must be a 3-letter ISO 4217 code")
        return value

    def validate_timezone(self,value):
        if value not in zoneinfo.available_timezones():
            raise serializers.ValidationError("Unknown IANA timezone name.")
        return value


class UserSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name",read_only=True,default=None)

    class Meta:
        model = User
        fields = [
            "id","email","full_name","role","company","company_name","is_staff"
        ]
        read_only_fields = fields


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(style={"input_type":"password"}, trim_whitespace=False)

class IntegrationConnectionSerializer(serializers.ModelSerializer):

    corrective_action = serializers.SerializerMethodField()

    class Meta:
        model = IntegrationConnection
        fields = [
            "id", "provider", "external_account_id", "status", "scopes",
                  "connected_at", "disconnected_at", "last_sync_at",
                  "token_expires_at", "corrective_action"
        ]
        read_only_fields = fields

    def get_corrective_action(self,obj) -> str:
        return{
            ConnectionStatus.CONNECTED: "",
            ConnectionStatus.DISCONNECTED: "Reconnect this integration to resume processing.",
            ConnectionStatus.EXPIRED: "Re-authorize — the provider token has expired.",
            ConnectionStatus.SYNC_FAILED: "Retry sync; if it persists, reconnect the integration.",

        }.get(obj.status, "")
    