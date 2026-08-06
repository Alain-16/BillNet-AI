from django.shortcuts import render

# Create your views here.
import io

from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from rest_framework import status as http
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsCompanyMember
from common.storage import get_object_storage
from documents.models import SourceDocument
from documents.serializers import SourceDocumentSerializer, UploadSerializer
from documents.services import ingest_upload
from expenses.serializers import ExpenseDetailSerializer
from expenses.services import process_document


class DocumentUploadView(APIView):

    permission_classes = [IsAuthenticated, IsCompanyMember]
    parser_classes = [MultiPartParser]

    def post(self, request):
        s = UploadSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        upload = s.validated_data["file"]

        document, created= ingest_upload(
            company=request.user.company, actor=request.user,
            data=upload.read(), filename=upload.name,
        )

        expense = process_document(document=document, actor=request.user)
        return Response(
            {
                "created": created,
                "document": SourceDocumentSerializer(document).data,
                "expense": ExpenseDetailSerializer(expense).data,
            }, 
            status=http.HTTP_201_CREATED if created else http.HTTP_200_OK,
        )


class DocumentContentView(APIView):
    permission_classes = [IsAuthenticated, IsCompanyMember]

    def get(self, request, pk):
        document = get_object_or_404(SourceDocument, pk=pk, company=request.user.company)

        try:
            data = get_object_storage().get(document.object_key)
        except FileNotFoundError:
            raise Http404("Stored evidence is missing")

        response = FileResponse(io.BytesIO(data), content_type=document.mime_type)

        response["Content-Disposition"] = f'inline; filename="{document.filename}"'
        response["Cache-Control"] = "private, no-store"
        return response

    