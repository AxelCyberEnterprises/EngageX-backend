from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (UserProfileViewSet, UserCreateViewSet, VerifyEmailView, GoogleLoginView,
                    CustomTokenCreateView, PasswordResetRequestView, PasswordResetConfirmView, CustomUserViewSet,
                    UpdateProfileView, ChangePasswordView, UserAssignmentViewSet)

router = DefaultRouter()
router.register(r'users', UserCreateViewSet, basename='user')
router.register(r'userprofiles', UserProfileViewSet, basename='userprofile')
router.register(r'assign', UserAssignmentViewSet, basename='user-assignment')


urlpatterns = [
    path('', include(router.urls)),  # Include all router-generated URLs
    path('auth/', include('djoser.urls.authtoken')),  # Token-based auth
    path('auth/login/', CustomTokenCreateView.as_view(), name='login'),
    path('auth/google-login/', GoogleLoginView.as_view(), name='google-login'),

    path('auth/password-reset/', PasswordResetRequestView.as_view(), name='password-reset-request'),
    path('auth/password-reset-confirm/', PasswordResetConfirmView.as_view(), name='password-reset-confirm'),
    path('auth/users/set_password/', CustomUserViewSet.as_view({'post': 'set_password'}), name='customuser-set-password'),
    path('password/change/', ChangePasswordView.as_view(), name='password-change'),
    path('auth/verify-email/', VerifyEmailView.as_view(), name='verify-email'), 

    path('profile/update/', UpdateProfileView.as_view(), name='profile-update'),
]