from rest_framework import serializers
from .models import PaymentTransaction
from django.contrib.auth import get_user_model

User = get_user_model()


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
