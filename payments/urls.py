from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import PaymentTransactionViewSet, intuit_auth, oauth_callback, intuit_index, clear_data_route, handle_payment_webhook, QuickbooksStatusAPIView

router = DefaultRouter()
router.register(r'transactions', PaymentTransactionViewSet, basename='payment-transaction')

app_name = 'payments'

urlpatterns = [
    path('index/', intuit_index, name='intuit_index'),

    path('intuit_auth/', intuit_auth, name='intuit_auth'),
    path('oauth_callback/', oauth_callback, name='oauth_callback'),

    path('clear-data/', clear_data_route, name='clear_data'),

    path('webhook/payment/', handle_payment_webhook, name='payment_callback'),
    path('status/', QuickbooksStatusAPIView.as_view(), name='quickbooks_status'),

    path('', include(router.urls)),
]
