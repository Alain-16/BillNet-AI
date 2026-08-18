from django.db import models


class ExpenseState(models.TextChoices):

    DISCOVERED = "DISCOVERED","discovered"
    FILE_PENDING = "FILE_PENDING", "File pending"
    CLASSIFICATION_PENDING = "CLASSIFICATION_PENDING", "Classification pending"
    EXTRACTION_PENDING = "EXTRACTION_PENDING", "Extraction pending"
    VALIDATION_PENDING = "VALIDATION_PENDING", "Validation pending"
    MATCHING_PENDING = "MATCHING_PENDING", "Matching pending"
    POLICY_PENDING = "POLICY_PENDING", "Policy pending"
    REVIEW_REQUIRED = "REVIEW_REQUIRED", "Review required"
    AUTO_POST_ELIGIBLE = "AUTO_POST_ELIGIBLE", "Auto-post eligible"
    BLOCKED = "BLOCKED", "Blocked"
    REJECTED = "REJECTED", "Rejected"
    APPROVED = "APPROVED", "Approved"
    POSTING_PENDING = "POSTING_PENDING", "Posting pending"
    POSTING_IN_PROGRESS = "POSTING_IN_PROGRESS", "Posting in progress"
    POSTED = "POSTED", "Posted"
    POSTING_FAILED = "POSTING_FAILED", "Posting failed"
    POSTING_UNKNOWN = "POSTING_UNKNOWN", "Posting outcome unknown"


class UserRole(models.TextChoices):
    OWNER = "OWNER", "Owner"
    ACCOUNTANT = "ACCOUNT", "accountant"

class Provider(models.TextChoices):
    GMAIL = "GMAIL","gmail"
    QUICKBOOKS = "QUICKBOOKS","quickbooks online"

class ConnectionStatus(models.TextChoices):
    CONNECTED = "CONNECTED","connected"
    DISCONNECTED = "DISCONNECTED","disconnected"
    EXPIRED = "EXPIRED","expired"
    SYNC_FAILED = "SYNC_FAILED","sync failed"


class CompanyStatus(models.TextChoices):
    ACTIVE = "ACTIVE","active"
    INACTIVE = "INACTIVE","inactive"
    SUSPENDED = "SUSPENDED","suspended"

class DocumentType(models.TextChoices):
    RECEIPT = "RECEIPT", "Receipt"
    PAID_INVOICE = "PAID_INVOICE", "Paid invoice"
    UNPAID_INVOICE = "UNPAID_INVOICE", "Unpaid invoice"
    REFUND_CREDIT = "REFUND_CREDIT", "Refund / credit"
    PURCHASE_ORDER = "PURCHASE_ORDER", "Purchase order"
    STATEMENT = "STATEMENT", "Statement"
    IRRELEVANT = "IRRELEVANT", "Irrelevant"
    UNKNOWN = "UNKNOWN", "Unknown"


class ClassificationMethod(models.TextChoices):
    RULE = "RULE", "Rule"
    AI = "AI", "AI"
    MANUAL = "MANUAL", "Manual"


class ExtractionMethod(models.TextChoices):
    EMBEDDED_TEXT = "EMBEDDED_TEXT", "Embedded PDF text"
    TEMPLATE = "TEMPLATE", "Known template"
    OCR = "OCR", "OCR"
    VISION_AI = "VISION_AI", "Vision AI"
    MANUAL = "MANUAL", "Manual entry"


class ExtractionStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    COMPLETED = "COMPLETED", "Completed"
    ABSTAINED = "ABSTAINED", "Abstained"   # model/OCR declined — safe, not a value
    FAILED = "FAILED", "Failed"


class ScanStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    CLEAN = "CLEAN", "Clean"
    INFECTED = "INFECTED", "Infected"
    FAILED = "FAILED", "Scan failed"


class AccountingRefType(models.TextChoices):
    COMPANY_INFO = "COMPANY_INFO", "Company info"
    VENDOR = "VENDOR", "Vendor"
    ACCOUNT = "ACCOUNT", "Account"
    CUSTOMER = "CUSTOMER", "Customer / project"
    ITEM = "ITEM", "Item"
    TAX_CODE = "TAX_CODE", "Tax code"
    PAYMENT_ACCOUNT = "PAYMENT_ACCOUNT", "Payment account"


class VendorStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"


class ProjectStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    CLOSED = "CLOSED", "Closed"
    INACTIVE = "INACTIVE", "Inactive"


class MappingScope(models.TextChoices):
    VENDOR = "VENDOR", "Vendor"
    PROJECT = "PROJECT", "Project"
    CATEGORY = "CATEGORY", "Expense category"
    TAX = "TAX", "Tax code"
    PAYMENT = "PAYMENT", "Payment account"


class PaymentType(models.TextChoices):
    CREDIT_CARD = "CREDIT_CARD", "Credit card"
    BANK = "BANK", "Bank / debit"       # Dev Guide decision #1: both from day one


class PolicyOutcome(models.TextChoices):
    REVIEW_REQUIRED = "REVIEW_REQUIRED", "Review required"
    AUTO_POST_ELIGIBLE = "AUTO_POST_ELIGIBLE", "Auto-post eligible"
    BLOCKED = "BLOCKED", "Blocked"
    REJECTED = "REJECTED", "Rejected"


class TransactionPurpose(models.TextChoices):
    PURCHASE = "PURCHASE", "Purchase"   # the ONE supported QBO entity in MVP


class PostingStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    CLAIMED = "CLAIMED", "Claimed"
    IN_PROGRESS = "IN_PROGRESS", "In progress"
    POSTED = "POSTED", "Posted"
    FAILED = "FAILED", "Failed"
    UNKNOWN = "UNKNOWN", "Unknown outcome"   # timeout — readback before any retry


class AuditActorType(models.TextChoices):
    SYSTEM = "SYSTEM", "System"
    USER = "USER", "User"
    AI = "AI", "AI"
    PROVIDER = "PROVIDER", "External provider"


class AuditEventType(models.TextChoices):
    DISCOVERY = "DISCOVERY", "Discovery"
    DOWNLOAD = "DOWNLOAD", "Download"
    FILE_CHECK = "FILE_CHECK", "File check"
    CLASSIFICATION = "CLASSIFICATION", "Classification"
    EXTRACTION = "EXTRACTION", "Extraction"
    MATCH = "MATCH", "Match"
    RULE_APPLIED = "RULE_APPLIED", "Rule applied"
    AI_CALL = "AI_CALL", "AI call"                 # folds AIInvocation
    CORRECTION = "CORRECTION", "Field correction"  # folds ReviewDecision (correct)
    APPROVAL = "APPROVAL", "Approval"              # folds ReviewDecision (approve)
    REJECTION = "REJECTION", "Rejection"           # folds ReviewDecision (reject)
    DUPLICATE_RESOLUTION = "DUPLICATE_RESOLUTION", "Duplicate resolution"
    POLICY_DECISION = "POLICY_DECISION", "Policy decision"
    POSTING_ATTEMPT = "POSTING_ATTEMPT", "Posting attempt"
    POSTING_RESULT = "POSTING_RESULT", "Posting result"
    EXCEPTION = "EXCEPTION", "Exception"
    RETRY = "RETRY", "Retry"
    CONFIG_UPDATED = "CONFIG_UPDATED", "Company config updated"
    INTEGRATION_CONNECTED = "INTEGRATION_CONNECTED", "Integration connected"
    INTEGRATION_REVOKED = "INTEGRATION_REVOKED", "Integration revoked"
    REFERENCE_SYNCED = "REFERENCE_SYNCED", "Reference data synced"
    PROJECT_CREATED = "PROJECT_CREATED", "Project created"
    PROJECT_UPDATED = "PROJECT_UPDATED", "Project updated"
    PROJECT_CLOSED = "PROJECT_CLOSED", "Project closed"
    PROJECT_LINKED = "PROJECT_LINKED", "Project linked to QBO customer"
    VALIDATION = "VALIDATION", "Validation run"
    STATE_CHANGED = "STATE_CHANGED", "Expense state changed"
    CATEGORIZATION= "CATEGORIZATION", "Categorization"


class ExceptionSeverity(models.TextChoices):
    LOW = "LOW", "Low"
    MEDIUM = "MEDIUM", "Medium"
    HIGH = "HIGH", "High"
    CRITICAL = "CRITICAL", "Critical"


class ExceptionStatus(models.TextChoices):
    OPEN = "OPEN", "Open"
    IN_PROGRESS = "IN_PROGRESS", "In progress"
    RESOLVED = "RESOLVED", "Resolved"
    IGNORED = "IGNORED", "Ignored"


class SourceDocumentKind(models.TextChoices):
    ATTACHMENT = "ATTACHMENT", "Attachment"
    EMAIL_BODY = "EMAIL_BODY", "Email body"


class MappingScope(models.TextChoices):
    VENDOR = "VENDOR", "Vendor"
    PROJECT = "PROJECT", "Project"
    CATEGORY = "CATEGORY", "Expense category"                # whole expense
    LINE_CATEGORY = "LINE_CATEGORY", "Line item category"    # NEW -- one line
    TAX = "TAX", "Tax code"
    PAYMENT = "PAYMENT", "Payment account"


class CategorizationStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    COMPLETED = "COMPLETED", "Completed"
    PARTIAL = "PARTIAL", "Partially resolved"
    SKIPPED = "SKIPPED", "Skipped"      # validation blocked, or feature disabled
    FAILED = "FAILED", "Failed"


class DecisionSource(models.TextChoices):
    """WHO decided. This is what gets surfaced in the payload so a reviewer can
    tell a rule they wrote from a model's guess -- those carry very different
    kinds of trust."""
    MAPPING_RULE = "MAPPING_RULE", "Company mapping rule"
    AI = "AI", "AI"
    HUMAN = "HUMAN", "Human"


class DecisionStatus(models.TextChoices):
    MATCHED = "MATCHED", "Matched"           # deterministic: a rule fired
    SUGGESTED = "SUGGESTED", "Suggested"     # AI proposed, human should confirm
    UNRESOLVED = "UNRESOLVED", "Unresolved"