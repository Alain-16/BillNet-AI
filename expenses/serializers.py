import zoneinfo 

from rest_framework import serializers

from expenses.models import Project
from documents.serializers import InterpretationSerializer, SourceDocumentSerializer
from expenses.models import Expense


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


class ExpenseListSerializer(serializers.ModelSerializer):
    project_code = serializers.CharField(source="project.code",read_only=True, default=None)

    error_count = serializers.SerializerMethodField()

    class Meta:
        model = Expense
        fields = [
            "id","state","vendor_raw_name","transaction_date","total","currency","project","project_code","error_count","version","created_at"
        ]
        read_only_fields = fields

    def get_error_count(self, obj) -> int:
        return sum(1 for c in (obj.validations or []) if c.get("severity") == "ERROR" and not c.get("passed"))


class ExpenseDetailSerializer(serializers.ModelSerializer):
    source_document = SourceDocumentSerializer(read_only=True)
    interpretation = InterpretationSerializer(read_only=True)
    evidence_url = serializers.SerializerMethodField()
    interpretation_history = serializers.SerializerMethodField()

    class Meta:
        model = Expense
        fields = [
            "id","state","version","currency","subtotal","tax_total","total","tax_breakdown","transaction_date","vendor_raw_name","receipt_number",
            "card_last_four","payment_type","memo","project","validations","source_document","interpretation","interpretation_history","evidence_url","created_at","updated_at"

        ]

        read_only_fields = fields

    def get_evidence_url(self, obj) -> str:
        return f"/api/v1/documents/{obj.source_document_id}/content"

    def get_interpretation_history(self, obj) -> list:
        return [{"id": str(i.id), "version": i.version,
                 "status": i.extraction_status, "method": i.extraction_method,
                 "created_at": i.created_at}
                for i in obj.source_document.interpretations.all()]


class ExpenseCorrectionSerializer(serializers.ModelSerializer):
    reason = serializers.CharField(required=False,allow_blank=True)
    version = serializers.IntegerField(required=False)

    class Meta:
        model = Expense
        fields = [
                "vendor_raw_name", "receipt_number", "transaction_date",
                  "currency", "subtotal", "tax_total", "total", "tax_breakdown",
                  "card_last_four", "payment_type", "memo", "project",
                  "reason", "version"
        ]
        extra_kwargs = {f: {"required": False} for f in fields}

