# from django.contrib import admin
# from .models import PaymentTransaction, QuickBooksToken, UserCredits


# @admin.register(PaymentTransaction)
# class PaymentTransactionAdmin(admin.ModelAdmin):
#     list_display = ('transaction_id', 'user', 'credits', 'status', 'created_at')
#     search_fields = ('transaction_id', 'user__email')
#     list_filter = ('status', 'created_at')


from django.contrib import admin
from .models import QuickBooksToken, PaymentTransaction, UserCredit

class QuickBooksTokenAdmin(admin.ModelAdmin):
    list_display = ('realm_id', 'expires_at', 'created_at', 'updated_at')

    search_fields = ('realm_id',)

admin.site.register(QuickBooksToken, QuickBooksTokenAdmin)

class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = ('transaction_id', 'realm_id', 'user', 'amount', 'credits', 'transaction_date', 'created_at')

    search_fields = ('transaction_id', 'realm_id', 'user__username', 'user__email', 'customer_name', 'customer_email')

    list_filter = ('realm_id', 'transaction_date')


admin.site.register(PaymentTransaction, PaymentTransactionAdmin)


class UserCreditAdmin(admin.ModelAdmin):
    list_display = ('user', 'credits', 'last_updated')

    search_fields = ('user__username', 'user__email')

admin.site.register(UserCredit, UserCreditAdmin)
