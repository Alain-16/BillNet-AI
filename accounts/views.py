from django.contrib.auth import authenticate, login, logout
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import IntegrationConnection
from accounts.permissions import IsCompanyMember, IsOwner
from accounts.providers import SLUG_TO_PROVIDER
from accounts.serializers import (
    CompanySerializer, IntegrationConnectionSerializer, LoginSerializer, UserSerializer,
)
from accounts.services import (
    complete_oauth, consume_oauth_state, revoke_integration, start_oauth,
    update_company_config,
)
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie

@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfCookieView(APIView):
    permission_classes = [AllowAny]

    def get(self,request):
        return Response({"detail":"CSRF cookie set"})



@method_decorator(ensure_csrf_cookie,name="dispatch")
class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self,request):
        s = LoginSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = authenticate(request,username=s.validated_data["email"],password=s.validated_data["password"])

        if user is None:
            return Response({"detail":"Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED)
        login(request, user)
        return Response(UserSerializer(user).data)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)

class CurrentUserView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)


class CompanyConfigView(APIView):
    def get_permissions(self):
        if self.request.method == "PATCH":
            return[IsAuthenticated(), IsOwner()]
        return [IsAuthenticated(), IsCompanyMember()]

    def get(self, request):
        return Response(CompanySerializer(request.user.company).data)

    def patch(self,request):
        s = CompanySerializer(request.user.company, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        company = update_company_config(company=request.user.company,actor=request.user, changes=s.validated_data)

        return Response(CompanySerializer(company).data)


def _resolve_provider(slug: str) -> str:
    provider = SLUG_TO_PROVIDER.get(slug)
    if provider is None:
        from rest_framework.exceptions import NotFound
        raise NotFound("Unknown provider.")
    return provider


class IntegrationConnectView(APIView):
    permission_classes = [IsAuthenticated, IsOwner]

    def post(self, request, provider):
        provider_key = _resolve_provider(provider)
        url = start_oauth(company=request.user.company, provider_key=provider_key,
                          actor=request.user)
        return Response({"authorization_url": url})


class IntegrationCallbackView(APIView):
    permission_classes = [IsAuthenticated, IsCompanyMember]

    def get(self, request, provider):
        provider_key = _resolve_provider(provider)
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        realm_id = request.query_params.get("realmId")   # QBO only
        meta = consume_oauth_state(state or "")
        # CSRF/binding checks: state must exist, match this provider, and this company.
        if (not code or not meta or meta["provider"] != provider_key
                or meta["company_id"] != str(request.user.company_id)):
            return Response({"detail": "Invalid or expired OAuth state."},
                            status=status.HTTP_400_BAD_REQUEST)
        conn = complete_oauth(company=request.user.company, provider_key=provider_key,
                              code=code, realm_id=realm_id, actor=request.user)
        return Response(IntegrationConnectionSerializer(conn).data)


class IntegrationListView(APIView):
    permission_classes = [IsAuthenticated, IsCompanyMember]

    def get(self, request):
        qs = IntegrationConnection.objects.filter(company=request.user.company)
        return Response(IntegrationConnectionSerializer(qs, many=True).data)


class IntegrationDetailView(APIView):
    permission_classes = [IsAuthenticated, IsOwner]

    def delete(self, request, pk):
        conn = get_object_or_404(IntegrationConnection, pk=pk, company=request.user.company)
        revoke_integration(conn=conn, actor=request.user)
        return Response(IntegrationConnectionSerializer(conn).data)


class IntegrationSyncView(APIView):
    permission_classes = [IsAuthenticated, IsOwner]

    def post(self, request, pk):
        conn = get_object_or_404(IntegrationConnection, pk=pk, company=request.user.company)
        from accounting.tasks import sync_qbo_references_task
        sync_qbo_references_task.delay(str(conn.id))  
        return Response({"detail": "Sync started."}, status=status.HTTP_202_ACCEPTED)




    










    