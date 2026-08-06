from django.shortcuts import get_object_or_404
from rest_framework import status as http
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounting.models import AccountingReference
from accounts.permissions import IsCompanyMember, IsOwner
from expenses.models import Project
from expenses.serializers import (
    ProjectClosesSerializer, ProjectLinkSerializer, ProjectSerializer,
)
from expenses.services import (
    close_project, create_project, link_qbo_customer, projects_active_on,
    update_project,
)


class ProjectListCreateView(APIView):
    # Read = any company member (a bookkeeper needs the list to review with).
    # Write = owner only.
    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), IsOwner()]
        return [IsAuthenticated(), IsCompanyMember()]

    def get(self, request):
        company = request.user.company
        active_on = request.query_params.get("active_on")     # ISO date
        if active_on:
            from django.utils.dateparse import parse_date
            on = parse_date(active_on)
            if on is None:
                return Response({"code": "bad_date",
                                 "detail": "active_on must be an ISO date."},
                                status=http.HTTP_400_BAD_REQUEST)
            qs = projects_active_on(company, on)
        else:
            qs = Project.objects.filter(company=company)
            state = request.query_params.get("status")
            if state:
                qs = qs.filter(status=state.upper())

        qs = qs.select_related("qbo_customer").order_by("code")
        return Response(ProjectSerializer(qs, many=True).data)

    def post(self, request):
        s = ProjectSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        project = create_project(
            company=request.user.company, actor=request.user,
            code=d["code"], name=d["name"], aliases=d.get("aliases"),
            active_from=d.get("active_from"), active_to=d.get("active_to"),
        )
        return Response(ProjectSerializer(project).data, status=http.HTTP_201_CREATED)


class ProjectDetailView(APIView):
    def get_permissions(self):
        if self.request.method == "PATCH":
            return [IsAuthenticated(), IsOwner()]
        return [IsAuthenticated(), IsCompanyMember()]

    def _get(self, request, pk) -> Project:
        # company= in the lookup IS the object-level authz check. A bare
        # get_object_or_404(Project, pk=pk) would leak across companies.
        return get_object_or_404(
            Project.objects.select_related("qbo_customer"),
            pk=pk, company=request.user.company,
        )

    def get(self, request, pk):
        return Response(ProjectSerializer(self._get(request, pk)).data)

    def patch(self, request, pk):
        project = self._get(request, pk)
        s = ProjectSerializer(project, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        expected = request.data.get("version")
        project = update_project(
            project=project, actor=request.user, changes=s.validated_data,
            expected_version=int(expected) if expected is not None else None,
        )
        return Response(ProjectSerializer(project).data)


class ProjectCloseView(APIView):
    permission_classes = [IsAuthenticated, IsOwner]

    def post(self, request, pk):
        project = get_object_or_404(Project, pk=pk, company=request.user.company)
        s = ProjectClosesSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        project = close_project(project=project, actor=request.user,
                                closed_on=s.validated_data.get("closed_on"))
        return Response(ProjectSerializer(project).data)


class ProjectLinkCustomerView(APIView):
    permission_classes = [IsAuthenticated, IsOwner]

    def post(self, request, pk):
        project = get_object_or_404(Project, pk=pk, company=request.user.company)
        s = ProjectLinkSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        # Scope the reference lookup to the company too -- belt and braces with
        # the service's own cross-company check.
        reference = get_object_or_404(
            AccountingReference, pk=s.validated_data["reference_id"],
            company=request.user.company,
        )
        project = link_qbo_customer(project=project, actor=request.user,
                                    reference=reference)
        return Response(ProjectSerializer(project).data)