from django.urls import path
from expenses import views as v


urlpatterns = [
    path("projects", v.ProjectListCreateView.as_view()),
    path("projects/<uuid:pk>", v.ProjectDetailView.as_view()),
    path("projects/<uuid:pk>/close", v.ProjectCloseView.as_view()),
    path("projects/<uuid:pk>/link-customer", v.ProjectLinkCustomerView.as_view()),
    path("expenses",v.ExpenseListView.as_view()),
    path("expenses/<uuid:pk>",v.ExpenseDetailView.as_view()),
    path("expenses/<uuid:pk>/approve", v.ExpenseApproveView.as_view()),
    path("expenses/<uuid:pk>/confirm-match", v.ExpenseConfirmMatchView.as_view()),
]