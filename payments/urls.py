from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import PaymentTransactionViewSet, stripe_checkout, stripe_webhook, StripeStatusAPIView, PaymentTransactionListView, UserTransactionsView

router = DefaultRouter()
router.register(r'transactions', PaymentTransactionViewSet)


app_name = 'payments'

urlpatterns = [
    path('', include(router.urls)),
    path('checkout/', stripe_checkout, name='stripe_checkout'),
    path('webhook/', stripe_webhook, name='stripe_webhook'),
    path('stripe/status/', StripeStatusAPIView.as_view(), name='stripe_status'),
    
    # Transaction views
    path('transactions/', PaymentTransactionListView.as_view(), name='transaction_list'),
    path('user/transactions/', UserTransactionsView.as_view(), name='user_transactions'),
    

]
