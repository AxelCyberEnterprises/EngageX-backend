
from django.contrib import admin
from .models import PaymentTransaction


class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = ('transaction_id', 'realm_id', 'user', 'amount', 'credits', 'transaction_date', 'created_at')
    search_fields = ('transaction_id', 'realm_id', 'user__username', 'user__email', 'customer_name', 'customer_email')
    list_filter = ('realm_id', 'transaction_date')


admin.site.register(PaymentTransaction, PaymentTransactionAdmin)
