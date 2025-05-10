import json
from django.shortcuts import get_object_or_404
from django.contrib.auth import get_user_model
from django.http import HttpResponse, JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated, IsAdminUser, AllowAny
from .models import PaymentTransaction
from .serializers import PaymentTransactionSerializer
from .stripe_utils import (
    create_checkout_session,
    handle_webhook,
    TIER_CREDITS,
    TIER_PRICE_IDS,
)

User = get_user_model()

@csrf_exempt
@require_http_methods(["POST"])
def stripe_checkout(request):
    """Create a Stripe checkout session."""
    try:
        data = json.loads(request.body.decode('utf-8'))
        price_id = data.get("priceId")
        email = data.get("email")
        tier = data.get("tier")
        
        if not price_id:
            return JsonResponse({"error": "Price ID is required"}, status=400)
        
        result, status_code = create_checkout_session(price_id, email, tier)
        return JsonResponse(result, status=status_code)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def stripe_webhook(request):
    """Handle Stripe webhook events."""
    try:
        payload = request.body
        sig_header = request.headers.get("STRIPE_SIGNATURE", "")
        
        if not sig_header:
            return JsonResponse({"error": "No Stripe signature header found"}, status=400)
        
        result, status_code = handle_webhook(payload, sig_header)
        return JsonResponse(result, status=status_code)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

class StripeStatusAPIView(APIView):
    """
    API view to get information about Stripe configuration.
    """
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        """
        Get Stripe status and configuration information.
        """
        from django.conf import settings
        stripe_public_key = getattr(settings, 'STRIPE_PUBLISHABLE_KEY', 'Not configured')
        
        # Build tier information for the frontend with both credits and price IDs
        tiers_info = {
            tier: {
                "credits": TIER_CREDITS.get(tier, 0),
                "priceId": TIER_PRICE_IDS.get(tier, ""),
            }
            for tier in TIER_CREDITS
        }
        
        return Response({
            'stripe_public_key': stripe_public_key,
            'is_configured': bool(stripe_public_key != 'Not configured'),
            'tiers': tiers_info,
            'success_url': getattr(settings, 'STRIPE_SUCCESS_URL', 'Not configured'),
            'cancel_url': getattr(settings, 'STRIPE_CANCEL_URL', 'Not configured'),
        })

class PaymentTransactionListView(APIView):
    """
    List all payment transactions for admin users.
    """
    permission_classes = [IsAuthenticated, IsAdminUser]
    
    def get(self, request, *args, **kwargs):
        transactions = PaymentTransaction.objects.all()
        serializer = PaymentTransactionSerializer(transactions, many=True)
        
        # Group credits by user
        user_credits = {}
        for transaction in transactions:
            if transaction.status == PaymentTransaction.STATUS_SUCCESS:
                user_id = transaction.user.id
                user_email = transaction.user.email
                if user_id not in user_credits:
                    user_credits[user_id] = {
                        'email': user_email,
                        'total_credits': 0
                    }
                user_credits[user_id]['total_credits'] += float(transaction.credits)
        
        return Response({
            "processed_transactions": serializer.data,
            "user_credits": user_credits
        })

class UserTransactionsView(APIView):
    """
    List payment transactions for the current user.
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request, *args, **kwargs):
        transactions = PaymentTransaction.objects.filter(user=request.user)
        serializer = PaymentTransactionSerializer(transactions, many=True)
        
        # Calculate total credits for the user
        total_credits = sum(
            float(t.credits) for t in transactions 
            if t.status == PaymentTransaction.STATUS_SUCCESS
        )
        
        return Response({
            "transactions": serializer.data,
            "total_credits": total_credits
        })

class PaymentTransactionViewSet(viewsets.ModelViewSet):
    """
    Admin viewset to monitor payment transactions.
    """
    queryset = PaymentTransaction.objects.all()
    serializer_class = PaymentTransactionSerializer
    permission_classes = [IsAuthenticated, IsAdminUser]
