from django.shortcuts import get_object_or_404
from rest_framework import status as http
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounting.models import AccountingReference
from accounts.permissions import IsCompanyMember, IsOwner
from expenses.models import Project, Expense
from expenses.serializers import (
    ProjectClosesSerializer, ProjectLinkSerializer, ProjectSerializer,ExpenseListSerializer,ExpenseDetailSerializer,ExpenseCorrectionSerializer
)
from expenses.services import (
    close_project, create_project, link_qbo_customer, projects_active_on,
    update_project,apply_corrections
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



class ExpenseListView(APIView):
    permission_classes = [IsAuthenticated, IsCompanyMember]

    def get(self, request):
        gs = (Expense.objects.filter(company=request.user.company).select_related("project","source_document").order_by("-created_at"))
        state = request.query_params.get("state")
        if state:
            gs = gs.filter(state=state.upper())
        return Response(ExpenseListSerializer(gs,many=True).data)

class ExpenseDetailView(APIView):
    permission_classes = [IsAuthenticated,IsCompanyMember]

    def _get(self,request,pk):
        return get_object_or_404(
            Expense.objects.select_related("source_document","interpretation","project"),
            pk=pk, company=request.user.company
        )

    def get(self, request, pk):
        return Response(ExpenseDetailSerializer(self._get(request, pk)).data)

    def patch(self, request, pk):
        expense = self._get(request,pk)
        s = ExpenseCorrectionSerializer(expense,data=request.data,partial=True)
        s.is_valid(raise_exception=True)
        data= dict(s.validated_data)
        reason = data.pop("reason","")
        version = data.pop("version", None)
        expense = apply_corrections(expense=expense,actor=request.user,changes=data, expected_version=version,reason=reason)
        return Response(ExpenseDetailSerializer(expense).data)