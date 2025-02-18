from rest_framework.permissions import BasePermission

from .models import UserProfile


class IsAdmin(BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            print("Permission denied: User not authenticated")
            return False

        if not hasattr(request.user, 'userprofile'):
            print(f"Permission denied: User {request.user} has no profile")
            return False

        user_role = request.user.userprofile.role
        print(f"User {request.user} role: {user_role}")

        return user_role == UserProfile.ADMIN
    

class IsPresenter(BasePermission):
    """Presenters can only access their own data."""
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.userprofile.is_presenter()

    def has_object_permission(self, request, view, obj):
        return obj.user == request.user


class IsCoach(BasePermission):
    """Coaches can access users assigned to them."""
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.userprofile.is_coach()

    def has_object_permission(self, request, view, obj):
        # Coaches can only access presenters they are assigned to
        return obj.user in request.user.assigned_presenters.all()
