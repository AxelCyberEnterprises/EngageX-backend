from django.urls import path, include
from rest_framework_nested import routers
from rest_framework.routers import DefaultRouter
from . import views
from .sso_views import SSOLoginRequestView, SSOLoginVerifyView
from .admin_views import ManualUserUploadView

router = DefaultRouter()
router.register(r'enterprises', views.EnterpriseViewSet, basename='enterprise')
router.register(r'enterprise-users', views.EnterpriseUserViewSet, basename='enterprise-user')
router.register(r'enterprise-questions', views.EnterpriseQuestionViewSet, basename='enterprise-question')

# Nested router for training goals under enterprises
enterprise_router = routers.NestedSimpleRouter(router, r'enterprises', lookup='enterprise')
enterprise_router.register(r'training-goals', views.TrainingGoalViewSet, basename='enterprise-training-goals')

urlpatterns = [
    path('', include(router.urls)),
    path('', include(enterprise_router.urls)),
    
    # SSO Authentication Endpoints
    path('sso/request-login/', SSOLoginRequestView.as_view(), name='sso-request-login'),
    path('sso/verify-login/', SSOLoginVerifyView.as_view(), name='sso-verify-login'),
    
    # Admin Endpoints
    path('admin/manual-upload/', ManualUserUploadView.as_view(), name='admin-manual-upload'),
]
