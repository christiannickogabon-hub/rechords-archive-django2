from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied


class StaffUserRequiredMixin(LoginRequiredMixin):
    """Any signed-in, active account. Read-only access to the archive."""


class AdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Administrators only. Everything that writes to the archive."""

    def test_func(self):
        return self.request.user.is_archive_admin

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            raise PermissionDenied("Administrator access is required for this page.")
        return super().handle_no_permission()


class AccountViewerRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Administrators and super administrators may see the account list."""

    def test_func(self):
        return self.request.user.can_view_accounts

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            raise PermissionDenied("Account access is required for this page.")
        return super().handle_no_permission()


class SuperAdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Account management — adding, editing and password resets — is the super administrator's."""

    def test_func(self):
        return self.request.user.is_super_admin

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            raise PermissionDenied("Super administrator access is required to manage accounts.")
        return super().handle_no_permission()


class OfficeNotifierRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Administrators and super administrators may notify an office."""

    def test_func(self):
        return self.request.user.can_view_accounts

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            raise PermissionDenied("Administrator access is required to notify an office.")
        return super().handle_no_permission()
