from django.urls import path
from documents import views as v

urlpatterns = [
    path("documents/upload",v.DocumentUploadView.as_view()),
    path("documents/<uuid:pk>/content",v.DocumentContentView.as_view()),
]