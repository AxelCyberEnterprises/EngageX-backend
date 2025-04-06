import django_filters
from .models import PracticeSession


class SessionDashboard(django_filters.FilterSet):

    class Meta:
        model = PracticeSession
        fields = []
