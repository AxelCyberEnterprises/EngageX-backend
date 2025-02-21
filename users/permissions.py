from rest_framework.permissions import BasePermission

from .models import UserProfile


class IsAdmin(BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if not hasattr(request.user, 'userprofile'):
            return False
        return request.user.userprofile.role == 'admin'
    

# class IsPresenter(BasePermission):
#     """Presenters can only access their own data."""
#     def has_permission(self, request, view):
#         return request.user.is_authenticated and request.user.userprofile.is_presenter()

#     def has_object_permission(self, request, view, obj):
#         return obj.user == request.user


# class IsCoach(BasePermission):
#     """Coaches can access users assigned to them."""
#     def has_permission(self, request, view):
#         return request.user.is_authenticated and request.user.userprofile.is_coach()

#     def has_object_permission(self, request, view, obj):
#         # Coaches can only access presenters they are assigned to
#         return obj.user in request.user.assigned_presenters.all()
