from django.urls import path

from accounts import views as v

urlpatterns = [
    path("auth/login",v.LoginView.as_view()),
    path("auth/logout",v.LogoutView.as_view()),
    path("auth/me",v.CurrentUserView.as_view()),
    path("company",v.CompanyConfigView.as_view()),
    path("integrations",v.IntegrationListView.as_view()),
    path("integrations/<str:provider>/connect",v.IntegrationConnectView.as_view()),
    path("integrations/<str:provider>/callback",v.IntegrationCallbackView.as_view()),
    path("integrations/<uuid:pk>",v.IntegrationDetailView.as_view()),
    path("integration/<uuid:pk>/sync", v.IntegrationSyncView.as_view()),
]