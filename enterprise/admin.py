from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from django.db import transaction
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from payments.models import Credit, CreditTransaction
from django import forms
from django.core.exceptions import ValidationError
from django.utils.html import format_html
from django.db import transaction
from .models import Enterprise, EnterpriseUser, EnterpriseQuestion, TrainingGoal

class CreditInline(admin.StackedInline):
    model = Credit
    can_delete = False
    verbose_name_plural = 'Credit Balance'
    fields = ('total_credits', 'credits_used', 'balance', 'updated_at')
    readonly_fields = ('balance', 'updated_at')
    extra = 0
    max_num = 1
    min_num = 1

    def has_delete_permission(self, request, obj=None):
        return False
        
    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        
        class CreditForm(formset.form):
            def clean(self):
                cleaned_data = super().clean()
                credits_used = cleaned_data.get('credits_used', 0)
                total_credits = cleaned_data.get('total_credits', 0)
                
                if credits_used < 0:
                    raise ValidationError({
                        'credits_used': 'Credits used cannot be negative.'
                    })
                if credits_used > total_credits: # Changed from total_used
                    raise ValidationError({
                        'credits_used': 'Credits used cannot exceed total credits.'
                    })
                return cleaned_data
        
        formset.form = CreditForm
        return formset


class EnterpriseQuestionInline(admin.TabularInline):
    model = EnterpriseQuestion
    extra = 1
    fields = ('question_text', 'vertical', 'is_active')
    show_change_link = True
    verbose_name_plural = 'Enterprise Questions'
    
    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        
        class EnterpriseQuestionForm(formset.form):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                if obj:
                    available_verticals = obj.get_available_verticals()
                    from enterprise.models import Enterprise
                    vertical_choices = []
                    for code in available_verticals:
                        try:
                            label = dict(Enterprise.Vertical.choices).get(code, code)
                            vertical_choices.append((code, label))
                        except (ValueError, AttributeError):
                            vertical_choices.append((code, code))
                    
                    self.fields['vertical'].choices = vertical_choices
                    
                    if self.instance and self.instance.pk:
                        current_vertical = self.instance.vertical
                        if not any(v[0] == current_vertical for v in vertical_choices):
                            self.fields['vertical'].choices.append(
                                (current_vertical, f"{current_vertical} (invalid for this enterprise type)")
                            )
        
        formset.form = EnterpriseQuestionForm
        return formset

@admin.register(Enterprise)
class EnterpriseAdmin(admin.ModelAdmin):
    list_display = ('name', 'enterprise_type_display', 'sport_type_display', 'current_credits_display', 'is_active', 'created_at')
    list_filter = ('is_active', 'enterprise_type', 'sport_type')
    search_fields = ('name',)
    readonly_fields = ('created_at', 'updated_at', 'available_verticals_display', 'current_credits_display')
    list_editable = ('is_active',)
    inlines = [CreditInline, EnterpriseQuestionInline]
    
    fieldsets = (
        (None, {
            'fields': ('name', 'enterprise_type', 'sport_type', 'logo', 'is_active')
        }),
        ('Verticals', {
            'fields': ('available_verticals_display',),
            'classes': ('collapse', 'wide'),
            'description': _('Available verticals based on enterprise type')
        }),
        ('Settings', {
            'fields': ('one_on_one_coaching_link',),
            'classes': ('collapse',),
            'description': _('Enterprise authentication settings')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def get_readonly_fields(self, request, obj=None):
        if obj and obj.questions.exists():
            return self.readonly_fields + ('enterprise_type',)
        return self.readonly_fields
    
    def save_model(self, request, obj, form, change):
        """Handle saving the Enterprise model"""
        super().save_model(request, obj, form, change)
    
    def save_formset(self, request, form, formset, change):
        """Validate that questions' verticals are valid for the enterprise type"""
        if formset.model == EnterpriseQuestion:
            for f in formset.forms:
                if f.cleaned_data and not f.cleaned_data.get('DELETE', False):
                    vertical = f.cleaned_data.get('vertical')
                    if vertical:
                        available_verticals = f.instance.enterprise.get_available_verticals()
                        if vertical not in available_verticals:
                            f.add_error('vertical', 
                                f"Vertical '{vertical}' is not available for this enterprise type"
                            )
                            raise ValidationError(
                                f"Cannot save: One or more questions have invalid verticals for this enterprise type"
                            )
        super().save_formset(request, form, formset, change)
    
    def available_verticals_display(self, obj):
        verticals = ", ".join([str(v.label) for v in obj.get_available_verticals()])
        return verticals if verticals else "-"
    available_verticals_display.short_description = 'Available Verticals'
    available_verticals_display.admin_order_field = 'enterprise_type'
    
    def current_credits_display(self, obj):
        credit = Credit.objects.filter(enterprise=obj).first()
        if credit:
            url = '{}?enterprise__id__exact={}'.format(
                reverse('admin:payments_credittransaction_changelist'),
                obj.id
            )
            balance = '{:.2f}'.format(float(credit.balance))
            return format_html(
                '<a href="{}">{} credits</a>',
                url,
                balance
            )
        return "0.00"
    current_credits_display.short_description = 'Available Credits'
    
    def enterprise_type_display(self, obj):
        color = '#4caf50' if obj.enterprise_type == 'sport' else '#2196f3'
        return format_html(
            '<span style="color: {}; font-weight: 500;">{}</span>',
            color,
            obj.get_enterprise_type_display()
        )
    enterprise_type_display.short_description = 'Type'
    enterprise_type_display.admin_order_field = 'enterprise_type'
    
    def sport_type_display(self, obj):
        if not obj.sport_type:
            return "-"
        sport_type_display = dict(Enterprise._meta.get_field('sport_type').choices).get(obj.sport_type, obj.sport_type)
        sport_colors = {
            'nfl': '#013369',
            'nba': '#1D428A',
            'wnba': '#FFCD34',
            'mlb': '#002D62'
        }
        color = sport_colors.get(obj.sport_type, '#757575')
        
        return format_html(
            '<span class="badge" style="background: {color}; color: white; padding: 3px 6px; border-radius: 4px; font-size: 12px;">{text}</span>',
            color=color,
            text=sport_type_display.upper()
        )
    sport_type_display.short_description = 'Sport Type'
    sport_type_display.admin_order_field = 'sport_type'

@admin.register(EnterpriseUser)
class EnterpriseUserAdmin(admin.ModelAdmin):
    list_display = ('user', 'enterprise', 'user_type', 'is_admin', 'created_at')
    list_filter = ('enterprise', 'user_type', 'is_admin')
    search_fields = ('user__email', 'enterprise__name')
    readonly_fields = ('created_at', 'updated_at')
    raw_id_fields = ('user',)
    
    fieldsets = (
            (None, {
                'fields': ('user', 'enterprise', 'user_type', 'is_admin')
            }),
            ('Timestamps', {
                'fields': ('created_at', 'updated_at'),
                'classes': ('collapse',)
            }),
        )


@admin.register(TrainingGoal)
class TrainingGoalAdmin(admin.ModelAdmin):
    list_display = ('enterprise', 'room_display', 'target_sessions_display', 'progress_display', 'due_date', 'is_active')
    list_filter = ('enterprise', 'is_active', 'room')
    search_fields = ('enterprise__name', 'room')
    list_editable = ('is_active',)
    list_display_links = ('enterprise', 'room_display')
    list_select_related = ('enterprise',)
    readonly_fields = ('created_at', 'updated_at', 'progress_display', 'is_completed_display')
    date_hierarchy = 'due_date'
    
    fieldsets = (
        (None, {
            'fields': ('enterprise', 'room', 'target_sessions', 'due_date', 'is_active')
        }),
        ('Progress', {
            'fields': ('progress_display', 'is_completed_display'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('enterprise')
    
    def room_display(self, obj):
        return obj.get_room_display()
    room_display.short_description = 'Room Type'
    room_display.admin_order_field = 'room'
    
    def target_sessions_display(self, obj):
        return obj.target_sessions or 'Not set'
    target_sessions_display.short_description = 'Target Sessions'
    target_sessions_display.admin_order_field = 'target_sessions'
    
    def progress_display(self, obj):
        if obj.target_sessions is None or obj.target_sessions == 0:
            return 'N/A (No target set)'
        try:
            progress = obj.progress_percent
            return f"{progress}%"
        except (TypeError, ZeroDivisionError):
            return 'N/A'
    progress_display.short_description = 'Progress'
    
    def is_completed_display(self, obj):
        if obj.target_sessions is None or obj.target_sessions == 0:
            return 'N/A (No target set)'
        return 'Yes' if obj.is_completed else 'No'
    is_completed_display.short_description = 'Completed?'
    
    def save_model(self, request, obj, form, change):
        with transaction.atomic():
            super().save_model(request, obj, form, change)
            Credit.objects.get_or_create(
                enterprise=obj,
                defaults={
                    'total_credits': 0,
                    'credits_used': 0
                }
            )


@admin.register(EnterpriseQuestion)
class EnterpriseQuestionAdmin(admin.ModelAdmin):
    list_display = ('truncated_question', 'enterprise_link', 'vertical_badge', 'gender_badge', 'sport_type_badge', 'audio_url_short', 'is_active', 'created_short')
    list_filter = ('enterprise__enterprise_type', 'vertical', 'sport_type', 'is_active', 'enterprise')
    search_fields = ('question_text', 'enterprise__name')
    list_editable = ('is_active',)
    list_select_related = ('enterprise',)
    readonly_fields = ('created_at', 'updated_at')
    list_per_page = 25
    
    fieldsets = (
        (None, {
            'fields': ('enterprise', 'vertical', 'question_text', 'audio_url', 'is_active')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('enterprise')
    
    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if obj is None:
            return form
        if obj and obj.enterprise:
            available_verticals = obj.enterprise.get_available_verticals()
            form.base_fields['vertical'].choices = [
                (v[0], v[1]) for v in available_verticals
            ]
            if obj.vertical and not any(v[0] == obj.vertical for v in available_verticals):
                form.base_fields['vertical'].choices.append(
                    (obj.vertical, f"{obj.vertical} (invalid for this enterprise type)")
                )
        return form
    
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'enterprise':
            kwargs['queryset'] = Enterprise.objects.order_by('name')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
    
    def save_model(self, request, obj, form, change):
        if obj.enterprise and obj.vertical:
            available_verticals = [v.value for v in obj.enterprise.get_available_verticals()]
            if obj.vertical not in available_verticals:
                raise ValidationError(
                    f"Vertical '{obj.vertical}' is not available for this enterprise type"
                )
        super().save_model(request, obj, form, change)
    
    def truncated_question(self, obj):
        max_length = 100
        if len(obj.question_text) > max_length:
            return f"{obj.question_text[:max_length]}..."
        return obj.question_text
    truncated_question.short_description = 'Question'
    
    def enterprise_link(self, obj):
        url = f"/admin/enterprise/enterprise/{obj.enterprise.id}/change/"
        return format_html('<a href="{}">{}</a>', url, obj.enterprise.name)
    enterprise_link.short_description = 'Enterprise'
    enterprise_link.admin_order_field = 'enterprise__name'
    
    def vertical_badge(self, obj):
        if not obj.vertical:
            return "-"
        vertical_display = dict(Enterprise.Vertical.choices).get(obj.vertical, obj.vertical)
        is_valid = any(v[0] == obj.vertical for v in obj.enterprise.get_available_verticals())
        color = '#4caf50' if is_valid else '#f44336'
        title = "" if is_valid else " (invalid for this enterprise type)"
        return format_html(
            '<span class="badge" style="background: {color}; color: white; padding: 3px 6px; border-radius: 4px; font-size: 12px;" title="{title}">{text}{title}</span>',
            color=color,
            title=title,
            text=vertical_display
        )
    vertical_badge.short_description = 'Vertical'
    vertical_badge.admin_order_field = 'vertical'
    
    def sport_type_badge(self, obj):
        if not obj.sport_type:
            return "-"
        sport_type_display = dict(EnterpriseQuestion._meta.get_field('sport_type').choices).get(obj.sport_type, obj.sport_type)
        sport_colors = {
            'nfl': '#013369',
            'nba': '#1D428A',
            'wnba': '#FFCD34',
            'mlb': '#002D62'
        }
        color = sport_colors.get(obj.sport_type, '#757575')
        return format_html(
            '<span class="badge" style="background: {color}; color: white; padding: 3px 6px; border-radius: 4px; font-size: 12px;">{text}</span>',
            color=color,
            text=sport_type_display.upper()
        )
    sport_type_badge.short_description = 'Sport Type'
    sport_type_badge.admin_order_field = 'sport_type'
    
    def audio_url_short(self, obj):
        if not obj.audio_url:
            return "-"
        return format_html(
            '<a href="{}" target="_blank">View Audio</a>',
            obj.audio_url
        )
    audio_url_short.short_description = 'Audio'
    audio_url_short.allow_tags = True
    
    def created_short(self, obj):
        return obj.created_at.strftime('%Y-%m-%d')
    created_short.short_description = 'Created'
    created_short.admin_order_field = 'created_at'
    
    def gender_badge(self, obj):
        if not obj.gender:
            return "-"
        gender_display = dict(EnterpriseQuestion.Gender.choices).get(obj.gender, obj.gender)
        color = '#4caf50'
        return format_html(
            '<span class="badge" style="background: {color}; color: white; padding: 3px 6px; border-radius: 4px; font-size: 12px;">{text}</span>',
            color=color,
            text=gender_display
        )
    gender_badge.short_description = 'Gender'
    gender_badge.admin_order_field = 'gender'