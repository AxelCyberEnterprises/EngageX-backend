from django.contrib import admin
from .models import PaymentTransaction


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = ('transaction_id', 'user', 'amount', 'credits', 'status', 'created_at')
    search_fields = ('transaction_id', 'user__email')
    list_filter = ('status', 'created_at')