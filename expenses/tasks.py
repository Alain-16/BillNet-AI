from celery import shared_task
from operations.categorization.service import categorize
from expenses.models import Expense
from accounts.models import Company
from operations.categorization.embedding import refresh_embeddings
from accounting.services import post_purchase
from accounting.services import attach_receipt

@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def categorize_expense_task(self, expense_id: str):

    expense = (Expense.objects
               .select_related("company","source_document")
               .get(pk=expense_id)
               )
    categorize(expense=expense)
    return{
        "expense_id": str(expense_id),
        "status": (expense.categorization or {}).get("status")
    }


@shared_task
def refresh_reference_embeddings_task(company_id: str):

    return refresh_embeddings(Company.objects.get(pk=company_id))


@shared_task(bind=True, max_retries=5, default_retry_delay=60)
def post_expense_task(self, intent_id: str):
    result = post_purchase(intent_id=intent_id, worker=self.request.hostname or "celery")
    if result.get("retryable"):
        raise self.retry()
    return result

@shared_task(bind=True, max_retries=5, default_retry_delay=120)
def attach_receipt_task(self, intent_id: str):
    try:
        return attach_receipt(intent_id=intent_id)
    except Exception as exc:
        raise self.retry(exc=exc)
    