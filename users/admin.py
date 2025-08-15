from django.contrib import admin
from django.contrib.auth.models import User
from .models import UserProfile, CustomUser, UserAssignment, ExpiringToken
from django.contrib.auth.admin import UserAdmin as DefaultUserAdmin
from django import forms
from django.utils import timezone


# Define a custom form for user creation and change
class UserCreationForm(forms.ModelForm):
    password1 = forms.CharField(widget=forms.PasswordInput())
    password2 = forms.CharField(widget=forms.PasswordInput())

    class Meta:
        model = CustomUser
        fields = (
            "username",
            "email",
            "first_name",
            "last_name",
        )

    def clean_password2(self):
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError("Passwords do not match")
        return password2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class UserChangeForm(forms.ModelForm):
    class Meta:
        model = CustomUser
        fields = (
            "username",
            "email",
            "first_name",
            "last_name",
            "is_active",
            "is_staff",
            "is_superuser",
        )


@admin.register(CustomUser)
class CustomUserAdmin(admin.ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm

    list_display = (
        "email",
        "username",
        "first_name",
        "last_name",
        "is_staff",
        "is_active",
    )
    search_fields = ("email", "username", "first_name", "last_name")
    list_filter = ("is_staff", "is_active")


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "age", "gender", "phone_number")
    search_fields = ("user__username", "gender", "phone_number")
    list_filter = ("gender", "phone_number")


class UserAssignmentAdmin(admin.ModelAdmin):
    list_display = ("admin", "user")
    search_fields = ("admin__username", "user__username")
    # list_filter = ('admin__userprofile__role', 'user__userprofile__role')


class ExpiringTokenAdmin(admin.ModelAdmin):
    list_display = ('key', 'user', 'created', 'expires_at', 'is_expired')
    fields = ('user', 'key', 'expires_at')
    readonly_fields = ('created', 'key')
    list_filter = ('expires_at',)
    search_fields = ('user__username', 'user__email', 'key')
    
    def is_expired(self, obj):
        if not obj.expires_at:
            return "Never"
        return obj.is_expired
    is_expired.boolean = True
    is_expired.short_description = 'Expired?'


admin.site.register(ExpiringToken, ExpiringTokenAdmin)
admin.site.register(UserAssignment, UserAssignmentAdmin)
