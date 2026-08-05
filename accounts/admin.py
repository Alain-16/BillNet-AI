from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import ReadOnlyPasswordHashField

from accounts.models import Company, IntegrationConnection, User


# ---- User needs custom add/change forms (the model has no `username` field) ----
class UserCreationForm(forms.ModelForm):
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ("email", "full_name", "company", "role")

    def clean_password2(self):
        p1, p2 = self.cleaned_data.get("password1"), self.cleaned_data.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Passwords don't match")
        return p2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])   # hash it
        if commit:
            user.save()
        return user


class UserChangeForm(forms.ModelForm):
    # Shows the hash read-only, with a link to the proper change-password form.
    password = ReadOnlyPasswordHashField(
        help_text="Raw passwords aren't stored. Use the change-password form."
    )

    class Meta:
        model = User
        fields = ("email", "password", "full_name", "company", "role",
                  "is_active", "is_staff", "is_superuser", "groups", "user_permissions")


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = UserCreationForm
    form = UserChangeForm
    list_display = ("email", "full_name", "company", "role", "is_staff", "is_active")
    list_filter = ("role", "is_staff", "is_active", "is_superuser")
    search_fields = ("email", "full_name")
    ordering = ("email",)
    filter_horizontal = ("groups", "user_permissions")
    readonly_fields = ("last_login",)
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("full_name", "company", "role")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser",
                                     "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login",)}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",),
                "fields": ("email", "full_name", "company", "role",
                           "password1", "password2", "is_staff", "is_superuser")}),
    )


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "currency", "timezone", "status", "auto_post_enabled")
    search_fields = ("name",)
    readonly_fields = ("id", "created_at", "updated_at")

    def save_model(self, request, obj, form, change):
        # Parity with create_company(): seed BC GST+PST on a NEW company so
        # admin-created companies aren't missing their tax profile.
        if not change and not obj.tax_codes:
            from accounts.services import seed_bc_tax_codes
            obj.tax_codes = seed_bc_tax_codes()
        super().save_model(request, obj, form, change)


@admin.register(IntegrationConnection)
class IntegrationConnectionAdmin(admin.ModelAdmin):
    list_display = ("provider", "external_account_id", "company", "status", "last_sync_at")
    list_filter = ("provider", "status")
    # Never surface the token blob in the admin form (even encrypted).
    exclude = ("encrypted_secret_ref",)
    readonly_fields = ("id", "token_expires_at", "connected_at", "disconnected_at",
                       "last_sync_at", "created_at", "updated_at")
