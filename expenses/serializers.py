import zoneinfo 

from rest_framework import serializers

from expenses.models import Project
from common.enums import ExtractionStatus
from expenses.models import Expense
from documents.models import SourceDocument
from common.enums import ExpenseState


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


class ReceiptDocumentSerializer(serializers.ModelSerializer):
    extraction_status = serializers.SerializerMethodField()
    extraction_method = serializers.SerializerMethodField()
    extraction_note = serializers.SerializerMethodField()
    evidence_url = serializers.SerializerMethodField()

    class Meta:
        model = SourceDocument
        fields = [
            "id","filename", "mime_type", "byte_size", "sha256", "scan_status",
            "extraction_status", "extraction_method", "extraction_note",
            "evidence_url", "created_at",
        ]
        read_only_fields = fields

    def _interpretation(self, obj):
        return self.context.get("interpretation") or obj.interpretations.first()

    def get_extraction_status(self,obj) -> str:
        interpretation = self._interpretation(obj)
        return interpretation.extraction_status if interpretation else ExtractionStatus.PENDING

    def get_extraction_method(self,obj) -> str:
        interpretation = self._interpretation(obj)
        return interpretation.extraction_method if interpretation else ""

    def get_extraction_note(self,obj) -> str:
        interpretation = self._interpretation(obj)
        return (interpretation.evidence or {}).get("detail", "") if interpretation else ""

    def get_evidence_url(self,obj) -> str:
        return f"/api/v1/documents/{obj.id}/content"

  
class ExtractedExpenseSerializer(serializers.ModelSerializer):

    class Meta:
        model = Expense
        fields = [
            "id", "state", "version",
            # --- workflow doc 3, in order ---
            "vendor_raw_name",                      # vendor
            "transaction_date",                     # date
            "line_items",                           # items
            "subtotal",                             # subtotal
            "tax_breakdown", "tax_total",           # taxes shown
            "total",                                # total
            "currency",                             # currency
            "payment_type", "card_last_four",       # payment clues
            # --- supporting ---
            "receipt_number", "memo",
            "created_at", "updated_at",
        ]
        read_only_fields = fields


class ExpenseCorrectionSerializer(serializers.ModelSerializer):
    reason = serializers.CharField(required=False,allow_blank=True)
    version = serializers.IntegerField(required=False)

    class Meta:
        model = Expense
        fields = [
                "vendor_raw_name", "receipt_number", "transaction_date",
                  "currency", "subtotal", "tax_total", "total", "tax_breakdown",
                  "card_last_four", "payment_type", "memo", "project","line_items",
                  "reason", "version"
        ]
        extra_kwargs = {f: {"required": False} for f in fields}

def build_validation_block(expense) -> dict:

    checks = expense.validations or []
    errors = [c for c in checks if c.get("severity") == "ERROR" and not c.get("passed")]
    warnings = [c for c in checks if c.get("severity") == "WARNING" and not c.get("passed")]

    return {
        "passed": not errors,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "checks": checks
    }


def build_categorization_block(expense) -> dict:

    return expense.categorization or {
        "schema_version":"categorization.v1",
        "status":"PENDING",
        "purchase_summary": "",
        "vendor": None,
        "project": None,
        "line_items": [],
    }

def build_posting_block(expense) -> dict:
    """The fifth top-level object: what happened in QuickBooks, and what the
    user must do next."""
    intent = expense.posting_intents.order_by("-created_at").first()
    if intent is None:
        return {"status": "NOT_POSTED", "next_action": None}

    block = {
        "status": intent.status,
        "qbo_purchase_id": intent.qbo_entity_id or None,
        "doc_number": intent.doc_number_token,
        "posted_at": intent.posted_at,
        "attachment_id": (intent.response_summary or {}).get("attachment_id"),
        "bank_matched_at": intent.bank_matched_at,
        "last_error": intent.last_error or None,
        "tax_treatment": (intent.approved_payload or {}).get("tax_treatment"),
    }

    if expense.state == ExpenseState.AWAITING_BANK_MATCH:
        
        block["next_action"] = {
            "code": "MATCH_BANK_FEED",
            "message": (
                f"Purchase {intent.qbo_entity_id} was created in QuickBooks for "
                f"{(intent.approved_payload or {}).get('total')} "
                f"{(intent.approved_payload or {}).get('currency')}. When this "
                f"charge appears in your bank feed, click MATCH -- not Add or "
                f"Categorize. Using Add would record the expense a second time."
            ),
        }
    return block


def build_receipt_payload(expense,*,created:bool | None = None) -> dict:

    return {
        **({"created": created} if created is not None else {}),
        "document":ReceiptDocumentSerializer(
            expense.source_document,
            context={"interpretation": expense.interpretation},
        ).data,
        "expense" : ExtractedExpenseSerializer(expense).data,
        "validation":build_validation_block(expense),
        "categorization":build_categorization_block(expense),
        "posting":build_posting_block(expense)
    }



class ApproveLineSerializer(serializers.Serializer):
    source_line = serializers.IntegerField()
    account_external_id = serializers.CharField(max_length=64)


class ApprovalSerializer(serializers.Serializer):
    version= serializers.IntegerField(required=False)
    payment_type=serializers.ChoiceField(choices=["BANK","CREDIT_CARD"],required=False)
    payment_account_external_id= serializers.CharField(max_length=64)
    vendor_external_id=serializers.CharField(max_length=64,allow_blank=True)
    project_external_id=serializers.CharField(required=False, allow_blank=True)
    memo=serializers.CharField(required=False,allow_blank=True)
    lines=ApproveLineSerializer(many=True)