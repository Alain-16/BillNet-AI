from rest_framework.permissions import BasePermission
from common.enums import UserRole

class IsCompanyMember(BasePermission):
    message = "You must be a company staff to access this resource"

    def has_permission(self, request, view) -> bool:
        u = request.user
        return bool(u and u.is_authenticated and u.company_id)


class IsOwner(BasePermission):

    message = "Only the company owner can perform this action"

    def has_permission(self, request, view) -> bool:

        u = request.user
        return bool(u and u.is_authenticated and u.company_id and u.role == UserRole.OWNER)

    
        