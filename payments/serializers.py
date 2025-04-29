# from rest_framework import serializers
# from .models import QuickBooksToken ,PaymentTransaction, UserCredit


# class PaymentTransactionSerializer(serializers.ModelSerializer):
#     user_email = serializers.EmailField(source="user.email", read_only=True)

#     class Meta:
#         model = PaymentTransaction
#         fields = [
#             'id', 'user_email', 'realm_id','transaction_id', 'transaction_date',
#             'credits', 'status', 'payment_gateway_response', 'customer_gateway_response',
#             'amount', 'currency', 'tier',
#             'created_at', 'updated_at'
#         ]

from rest_framework import serializers
from .models import QuickBooksToken, PaymentTransaction
from django.contrib.auth import get_user_model

User = get_user_model()

class QuickBooksTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuickBooksToken
        # Include non-sensitive fields
        fields = [
            'id',
            'realm_id',
            'expires_at',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields 

# --- Serializer for PaymentTransaction ---
class PaymentTransactionSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = PaymentTransaction
        fields = [
            'id',
            'user',
            'user_email',
            'realm_id',
            'transaction_id',
            'transaction_date',
            'customer_name',
            'customer_email',
            'amount',
            'currency',
            'status',
            'tier',
            'credits',
            'payment_gateway_response',
            'customer_gateway_response',
            'created_at',
            'updated_at',
        ]

        # read_only_fields = [
        #      'id',
        #      'user_email',
        #      'realm_id',
        #      'transaction_id',
        #      'transaction_date',
        #      'customer_name',
        #      'customer_email',
        #      'amount',
        #      'currency',
        #      'status',
        #      'credits_added',
        #      'payment_gateway_response',
        #      'customer_gateway_response',
        #      'created_at',
        #      'updated_at',
        # ]
