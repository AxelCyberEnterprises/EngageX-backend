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
    SessionList,
    CompareSessionsView,
    GoalAchievementView,
    PerformanceMetricsComparison,
    ImproveExistingSequence, ImproveNewSequence,
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
from django.urls import path
from .views import get_openai_realtime_token

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
    path("compare-sequences/<str:sequence_id>/",PerformanceMetricsComparison.as_view(),name="compare-sequences"),

    path(
        "sessions-list/",
        SessionList.as_view(),
        name="sessions-list",
    ),
    path(
        "compare-sessions/<str:session1_id>/<str:session2_id>/",
        CompareSessionsView.as_view(),
        name="compare-sessions",
    ),
    path(
        "goals-and-achievement/",
        GoalAchievementView.as_view(),
        name="goals-and-achievements",
    ),
    path("improve-existing-sequence/",ImproveExistingSequence.as_view(),name="improve-existing-sequence"),

    path("improve-new-sequence/<str:session_id>/",ImproveNewSequence.as_view(),name="improve-new-sequence"),
    path("improve-new-sequence/",ImproveNewSequence.as_view(),name="sessionlistwithoutsequence"),
    path("api/openai/realtime-token/", get_openai_realtime_token),

]
