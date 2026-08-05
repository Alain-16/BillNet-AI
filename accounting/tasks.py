from celery import shared_task

from accounts.models import IntegrationConnection
from accounting.services import sync_references


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def sync_qbo_references_task(self, connection_id):
    # Idempotent handler (Invariant #3): safe to redeliver.
    conn = IntegrationConnection.objects.get(id=connection_id)
    try:
        return sync_references(conn)
    except Exception as exc:
        raise self.retry(exc=exc)