from rest_framework import serializers

from documents.models import DocumentInterpretation, SourceDocument


class SourceDocumentSerializer(serializers.ModelSerializer):

    class Meta:
        model = SourceDocument
        fields = [
            "id","kind","filename","mime_type","byte_size","sha256","scan_status","created_at"
        ]
        read_only_fields = fields


class InterpretationSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentInterpretation
        fields = [
            "id","version","document_type","classification_method","extraction_method","extraction_status","schema_version","latency_ms","fields","evidence","created_at"
        ]
        read_only_fields = fields


class UploadSerializer(serializers.Serializer):
    file = serializers.FileField()