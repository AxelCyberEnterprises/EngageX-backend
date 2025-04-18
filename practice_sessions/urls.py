from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    PracticeSessionViewSet,
    SessionDashboardView,
    UploadSessionSlidesView,
    ChunkSentimentAnalysisViewSet,
    SessionChunkViewSet,
    SessionReportView,
    PerformanceAnalyticsView,
    SequenceListView,
    SessionsInSequenceView,
    CompareSessionsView,
    GoalAchievementView,
)

router = DefaultRouter()
router.register(r"sessions", PracticeSessionViewSet, basename="practice-session")

router.register(
    r"session_chunks", SessionChunkViewSet, basename="session-chunk"
)  # Register the new ViewSet
router.register(
    r"chunk_sentiment_analysis",
    ChunkSentimentAnalysisViewSet,
    basename="chunk-sentiment-analysis",
)

urlpatterns = [
    path("", include(router.urls)),
    path("dashboard/", SessionDashboardView.as_view(), name="session-dashboard"),
    path(
        "practice-sessions/<int:pk>/upload-slides/",
        UploadSessionSlidesView.as_view(),
        name="practice-session-upload-slides",
    ),
    path(
        "sessions-report/<int:session_id>/",
        SessionReportView.as_view(),
        name="chunk-summary",
    ),
    path(
        "performance-analytics/",
        PerformanceAnalyticsView.as_view(),
        name="performance-analytics",
    ),
    path("sequences/", SequenceListView.as_view(), name="sequence-list"),
    path(
        "sequences/<str:sequence_id>/sessions/",
        SessionsInSequenceView.as_view(),
        name="sessions-in-sequence",
    ),
    path(
        "compare-sessions/<str:session1_id>/<str:session2_id>",
        CompareSessionsView.as_view(),
        name="compare-sessions",
    ),
    path(
        "goals-and-achievement/",
        GoalAchievementView.as_view(),
        name="goals-and-achievmente",
    ),
]
