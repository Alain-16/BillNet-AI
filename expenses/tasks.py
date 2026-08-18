from celery import shared_task
from operations.categorization.service import categorize
from expenses.models import Expense
from accounts.models import Company
from operations.categorization.embedding import refresh_embeddings

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


