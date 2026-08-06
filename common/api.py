from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler
from common.errors import DomainError

def api_exception_handler(exc, context):
    if isinstance(exc,DomainError):
        body = {"code": exc.code,"detail":exc.message}
        if exc.field:
            body["field"] = exc.field
        return Response(body, status=status.HTTP_400_BAD_REQUEST)
    return drf_exception_handler(exc,context)