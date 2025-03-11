from django.contrib import admin
from django.contrib.auth.models import User
from .models import (UserProfile, CustomUser, UserAssignment)
from django.contrib.auth.admin import UserAdmin as DefaultUserAdmin
from django import forms


# Define a custom form for user creation and change
class UserCreationForm(forms.ModelForm):
    password1 = forms.CharField(widget=forms.PasswordInput())
    password2 = forms.CharField(widget=forms.PasswordInput())

    class Meta:
        model = CustomUser
        fields = ('username', 'email', 'first_name', 'last_name',)

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
        fields = ('username', 'email', 'first_name', 'last_name', 'is_active', 'is_staff', 'is_superuser')


@admin.register(CustomUser)
class CustomUserAdmin(admin.ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm

    list_display = ('email', 'username', 'first_name', 'last_name', 'is_staff', 'is_active')
    search_fields = ('email', 'username',  'first_name', 'last_name')
    list_filter = ('is_staff', 'is_active')


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'age', 'gender', 'phone_number')
    search_fields = ('user__username', 'gender',  'phone_number')
    list_filter = ('gender', 'phone_number')



class UserAssignmentAdmin(admin.ModelAdmin):
    list_display = ('admin', 'user')
    search_fields = ('admin__username', 'user__username')
    list_filter = ('admin__userprofile__role', 'user__userprofile__role')

admin.site.register(UserAssignment, UserAssignmentAdmin)