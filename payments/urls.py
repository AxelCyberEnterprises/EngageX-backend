from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import PaymentCallbackView, PaymentTransactionViewSet, intuit_auth, intuit_oauth_callback, intuit_index, clear_data_route, handle_payment_webhook, QuickbooksStatusAPIView

router = DefaultRouter()
router.register(r'transactions', PaymentTransactionViewSet, basename='payment-transaction')

urlpatterns = [
    path('index/', intuit_index, name='intuit-index'),
    path('auth/', intuit_auth, name='intuit-auth'),
    path('auth/callback/', intuit_oauth_callback, name='intuit-oauth-callback'),
    # path('callback/', PaymentCallbackView.as_view(), name='payment-callback'),
    # e.g., /payments/clear-data/
    path('clear-data/', clear_data_route, name='clear_data'),

    path('webhook/payment/', handle_payment_webhook, name='payment-callback'),

    path('status/', QuickbooksStatusAPIView.as_view(), name='quickbooks-status'),
    path('', include(router.urls)),
]
