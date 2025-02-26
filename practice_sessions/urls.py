from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import PracticeSessionViewSet, SessionDashboardView

router = DefaultRouter()
router.register(r'sessions', PracticeSessionViewSet, basename='practice-session')

urlpatterns = [
    path('', include(router.urls)),
    path('dashboard/', SessionDashboardView.as_view(), name='session-dashboard'),
]
