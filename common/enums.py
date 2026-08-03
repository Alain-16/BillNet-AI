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