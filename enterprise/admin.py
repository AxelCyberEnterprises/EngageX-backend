from django.contrib import admin
from .models import Enterprise, EnterpriseUser

@admin.register(Enterprise)
class EnterpriseAdmin(admin.ModelAdmin):
    list_display = ('name', 'domain', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'domain')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        (None, {
            'fields': ('name', 'domain', 'logo', 'is_active')
        }),
        ('Settings', {
            'fields': ('require_domain_match',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

@admin.register(EnterpriseUser)
class EnterpriseUserAdmin(admin.ModelAdmin):
    list_display = ('user', 'enterprise', 'user_type', 'is_admin', 'created_at')
    list_filter = ('enterprise', 'user_type', 'is_admin')
    search_fields = ('user__email', 'enterprise__name', 'department', 'position')
    readonly_fields = ('created_at', 'updated_at')
    raw_id_fields = ('user',)
    
    fieldsets = (
        (None, {
            'fields': ('user', 'enterprise', 'user_type', 'is_admin')
        }),
        ('Additional Information', {
            'fields': ('department', 'position'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
