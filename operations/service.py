from __future__ import annotations
from typing import Any

from common.enums import AuditActorType
from operations.models import AuditEvent



def record_event(*, company, event_type:str,aggregate_type:str, aggregate_id:str,
                 actor_type:str= AuditActorType.SYSTEM, actor_id:str="",payload:dict[str,Any] | None = None, correlation_id: str ="",object_version: int | None = None) -> AuditEvent:

    return AuditEvent.objects.create(
        company=company, event_type=event_type, actor_type=actor_type, actor_id=str(actor_id or ""), aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id), object_version=object_version, correlation_id=correlation_id, payload=payload or {},
    )


                 