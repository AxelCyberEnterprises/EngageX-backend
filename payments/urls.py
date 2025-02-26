from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import PaymentCallbackView, PaymentTransactionViewSet

router = DefaultRouter()
router.register(r'transactions', PaymentTransactionViewSet, basename='payment-transaction')

urlpatterns = [
    path('callback/', PaymentCallbackView.as_view(), name='payment-callback'),
    path('', include(router.urls)),
]
