from django.contrib import messages
from datetime import datetime

from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.forms import AdminPasswordChangeForm, PasswordChangeForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, FormView, ListView, UpdateView, View

from documents.models import AccessLog, Document, Notification

from . import brevo, passwordreset, twofactor
from .forms import (
    AdminSetPasswordForm,
    ArchiveLoginForm,
    OfficeForm,
    ForgotPasswordForm,
    OfficeNotifyForm,
    ProfileForm,
    ResetPasswordForm,
    SignUpForm,
    SecondFactorForm,
    UserCreateForm,
    UserMessageForm,
    UserUpdateForm,
)
from .mixins import (
    AccountViewerRequiredMixin,
    AdminRequiredMixin,
    OfficeNotifierRequiredMixin,
    StaffUserRequiredMixin,
    SuperAdminRequiredMixin,
)
from .models import Office, User


class ArchiveLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = ArchiveLoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        user = form.get_user()
        if user.needs_second_factor:
            # Password accepted, but the session stays anonymous until the code is verified.
            self.request.session[twofactor.PENDING_USER_KEY] = user.pk
            self.request.session[twofactor.PENDING_STARTED_KEY] = timezone.now().isoformat()
            self.request.session[twofactor.PENDING_ATTEMPTS_KEY] = 0
            self.request.session["pending_2fa_next"] = self.get_success_url()
            return redirect("accounts:verify")

        response = super().form_valid(form)
        AccessLog.record(self.request, action=AccessLog.Action.LOGIN)
        return response

    def form_invalid(self, form):
        if form.account_disabled:
            AccessLog.record(
                self.request,
                action=AccessLog.Action.DENIED,
                note=f"Sign-in attempt on disabled account {form.cleaned_data.get('username')}",
            )
        return super().form_invalid(form)


class ArchiveLogoutView(LogoutView):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            AccessLog.record(request, action=AccessLog.Action.LOGOUT)
        return super().dispatch(request, *args, **kwargs)


class ProfileView(LoginRequiredMixin, UpdateView):
    model = User
    form_class = ProfileForm
    template_name = "accounts/profile.html"
    success_url = reverse_lazy("accounts:profile")

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Profile updated.")
        return response


class ChangeOwnPasswordView(LoginRequiredMixin, FormView):
    template_name = "accounts/password_change.html"
    form_class = PasswordChangeForm
    success_url = reverse_lazy("documents:dashboard")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.save()
        update_session_auth_hash(self.request, form.user)
        messages.success(self.request, "Password changed.")
        return super().form_valid(form)


class UserListView(AccountViewerRequiredMixin, ListView):
    model = User
    template_name = "accounts/user_list.html"
    context_object_name = "users"
    paginate_by = 25

    def get_queryset(self):
        qs = super().get_queryset()
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(username__icontains=q) | qs.filter(last_name__icontains=q) | qs.filter(office__name__icontains=q)
        return qs.distinct()


class UserCreateView(SuperAdminRequiredMixin, CreateView):
    model = User
    form_class = UserCreateForm
    template_name = "accounts/user_form.html"
    success_url = reverse_lazy("accounts:user_list")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Account created for {self.object.username}.")
        AccessLog.record(
            self.request,
            action=AccessLog.Action.USER_MANAGE,
            note=f"Created account {self.object.username} ({self.object.role_label})",
        )
        return response


class UserUpdateView(SuperAdminRequiredMixin, UpdateView):
    model = User
    form_class = UserUpdateForm
    template_name = "accounts/user_form.html"
    success_url = reverse_lazy("accounts:user_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["editing_user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Account {self.object.username} updated.")
        AccessLog.record(
            self.request,
            action=AccessLog.Action.USER_MANAGE,
            note=f"Updated account {self.object.username} ({self.object.role_label})",
        )
        return response


class UserPasswordResetView(SuperAdminRequiredMixin, FormView):
    template_name = "accounts/user_password.html"
    form_class = AdminSetPasswordForm
    success_url = reverse_lazy("accounts:user_list")

    def get_target(self):
        return get_object_or_404(User, pk=self.kwargs["pk"])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.get_target()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["target_user"] = self.get_target()
        return context

    def form_valid(self, form):
        form.save()
        bypassed = form.cleaned_data.get("bypass_validation")
        messages.success(self.request, f"New password set for {form.user.username}.")
        if bypassed:
            messages.warning(
                self.request,
                f"Password requirements were bypassed — ask {form.user.username} to change it after signing in.",
            )
        AccessLog.record(
            self.request,
            action=AccessLog.Action.USER_MANAGE,
            note=f"Reset password for {form.user.username}"
                 + (" (password requirements bypassed)" if bypassed else ""),
        )
        return super().form_valid(form)


class NotificationListView(LoginRequiredMixin, ListView):
    template_name = "accounts/notification_list.html"
    context_object_name = "notification_list"
    paginate_by = 30

    def get_queryset(self):
        return self.request.user.notifications.select_related("document", "actor")

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        request.user.notifications.filter(is_read=False).update(is_read=True)
        return response


def notifications_poll(request):
    """Polled every few seconds by the bell icon; returns a rendered fragment plus an unread count header."""
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    items = request.user.notifications.select_related("document", "actor")[:8]
    unread = request.user.notifications.filter(is_read=False).count()
    html = render_to_string(
        "accounts/_notification_items.html", {"notification_items": items}, request=request
    )
    response = HttpResponse(html)
    response["X-Unread-Count"] = str(unread)
    return response


def notification_open(request, pk):
    if not request.user.is_authenticated:
        return redirect("accounts:login")
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=["is_read"])
    return redirect(notification.get_absolute_url() or "documents:dashboard")


def notifications_mark_all_read(request):
    if not request.user.is_authenticated:
        return redirect("accounts:login")
    request.user.notifications.filter(is_read=False).update(is_read=True)
    return redirect(request.META.get("HTTP_REFERER") or "documents:dashboard")


class OfficeListView(StaffUserRequiredMixin, ListView):
    """Every office in the hall. Staff can read it; admins act on it."""

    template_name = "accounts/office_list.html"
    context_object_name = "offices"

    def get_queryset(self):
        return Office.objects.annotate(
            member_count=Count("members", filter=Q(members__is_active=True))
        )


class OfficeCreateView(AdminRequiredMixin, CreateView):
    model = Office
    form_class = OfficeForm
    template_name = "accounts/office_form.html"
    success_url = reverse_lazy("accounts:office_list")

    def form_valid(self, form):
        response = super().form_valid(form)
        AccessLog.record(self.request, AccessLog.Action.USER_MANAGE, note=f"Added office {self.object.name}")
        messages.success(self.request, f"{self.object.name} added.")
        return response

    def get_context_data(self, **kwargs):
        return super().get_context_data(heading="Add an office", **kwargs)


class OfficeUpdateView(AdminRequiredMixin, UpdateView):
    model = Office
    form_class = OfficeForm
    template_name = "accounts/office_form.html"
    success_url = reverse_lazy("accounts:office_list")

    def form_valid(self, form):
        response = super().form_valid(form)
        AccessLog.record(self.request, AccessLog.Action.USER_MANAGE, note=f"Edited office {self.object.name}")
        messages.success(self.request, f"{self.object.name} updated.")
        return response

    def get_context_data(self, **kwargs):
        return super().get_context_data(heading=f"Edit {self.object.name}", **kwargs)


class OfficeNotifyView(OfficeNotifierRequiredMixin, FormView):
    """Send one notification to every active member of a single office."""

    template_name = "accounts/office_notify.html"
    form_class = OfficeNotifyForm

    def get_office(self):
        return get_object_or_404(Office, pk=self.kwargs["pk"])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["office"] = self.get_office()
        context["recipients"] = context["office"].active_members.order_by("last_name", "username")
        return context

    def form_valid(self, form):
        office = self.get_office()
        recipients = list(office.active_members.exclude(pk=self.request.user.pk))
        if not recipients:
            messages.error(self.request, f"{office.name} has no active members to notify.")
            return redirect("accounts:office_list")

        body = form.cleaned_data["message"]
        Notification.notify(
            recipients=recipients,
            actor=self.request.user,
            verb=f"[{office.name}] {body}",
        )

        emailed, no_address, failures = 0, 0, []
        if brevo.is_configured():
            subject = f"{office.name} — notice from the Document Archive"
            for member in recipients:
                if not member.email:
                    no_address += 1
                    continue
                html = (
                    f"<p>Hello {member.get_full_name() or member.username},</p>"
                    f"<p>{body}</p>"
                    f"<p style='color:#64748b;font-size:13px'>Sent to {office.name} by "
                    f"{self.request.user.get_full_name() or self.request.user.username}, "
                    f"Municipality of Carigara Document Archiving System.</p>"
                )
                ok, error = brevo.send_email(member.email, member.get_full_name(), subject, html, body)
                if ok:
                    emailed += 1
                elif error not in failures:
                    failures.append(error)

        AccessLog.record(
            self.request,
            AccessLog.Action.USER_MANAGE,
            note=(f"Notified {office.name} ({len(recipients)} recipient"
                  f"{'s' if len(recipients) != 1 else ''}, {emailed} by email)"),
        )
        messages.success(
            self.request,
            f"Sent to {len(recipients)} member{'s' if len(recipients) != 1 else ''} of {office.name}"
            + (f", {emailed} also by email" if emailed else "") + ".",
        )
        if no_address:
            messages.warning(
                self.request,
                f"{no_address} member{'s have' if no_address != 1 else ' has'} no email address on file, "
                "so they were not emailed.",
            )
        for error in failures:
            messages.error(self.request, f"Emails could not be sent: {error}")
        return redirect("accounts:office_list")


class UserMessageView(AccountViewerRequiredMixin, FormView):
    """Send one person a note about a record that is already filed.

    This is the alternative to re-uploading a document that only needs a
    correction or a remark: the message carries a link to the record instead.
    """

    template_name = "accounts/user_message.html"
    form_class = UserMessageForm

    def get_recipient(self):
        return get_object_or_404(User, pk=self.kwargs["pk"])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["documents"] = Document.objects.visible_to(self.request.user).order_by("-date_issued")
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        document = self.request.GET.get("document")
        if document:
            initial["document"] = document
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["recipient"] = self.get_recipient()
        return context

    def form_valid(self, form):
        recipient = self.get_recipient()
        if recipient.pk == self.request.user.pk:
            messages.error(self.request, "You cannot send a message to yourself.")
            return redirect("accounts:user_list")
        if not recipient.is_active:
            messages.error(self.request, f"{recipient.username} is disabled and cannot be messaged.")
            return redirect("accounts:user_list")

        subject = form.cleaned_data["subject"]
        body = form.cleaned_data["message"]
        document = form.cleaned_data.get("document")

        verb = f"{subject} — {body}"
        link = ""
        if document:
            verb = f"{document.control_number}: {subject} — {body}"
            link = self.request.build_absolute_uri(document.get_absolute_url())

        Notification.notify(
            recipients=[recipient],
            actor=self.request.user,
            verb=verb,
            document=document,
            email_subject=f"Message from the Document Archive — {subject}",
            link=link,
        )
        AccessLog.record(
            self.request,
            AccessLog.Action.USER_MANAGE,
            document=document,
            note=f"Messaged {recipient.username}: {subject}"[:255],
        )
        if recipient.email:
            messages.success(
                self.request,
                f"Sent to {recipient.get_full_name() or recipient.username}, "
                "in the archive and by email.",
            )
        else:
            messages.success(
                self.request,
                f"Sent to {recipient.get_full_name() or recipient.username} in the archive. "
                "No email address is on file, so nothing was emailed.",
            )
        return redirect("accounts:user_list")


class SecondFactorView(FormView):
    """Step two of a super administrator's sign-in: a one-time code sent by email."""

    template_name = "accounts/verify.html"
    form_class = SecondFactorForm

    def dispatch(self, request, *args, **kwargs):
        self.pending = self.get_pending_user()
        if self.pending is None:
            messages.error(request, "That sign-in expired. Please enter your username and password again.")
            return redirect("accounts:login")
        return super().dispatch(request, *args, **kwargs)

    def get_pending_user(self):
        pk = self.request.session.get(twofactor.PENDING_USER_KEY)
        started = self.request.session.get(twofactor.PENDING_STARTED_KEY)
        if not pk or not started:
            return None
        age = (timezone.now() - datetime.fromisoformat(started)).total_seconds()
        if age > twofactor.PENDING_TIMEOUT_SECONDS:
            self.clear_pending()
            return None
        user = User.objects.filter(pk=pk, is_active=True).first()
        return user if user and user.needs_second_factor else None

    def clear_pending(self):
        for key in (twofactor.PENDING_USER_KEY, twofactor.PENDING_STARTED_KEY,
                    twofactor.PENDING_ATTEMPTS_KEY, "pending_2fa_next"):
            self.request.session.pop(key, None)

    def get(self, request, *args, **kwargs):
        # Send a code on arrival, and whenever the last one has expired.
        if not twofactor.code_is_fresh(self.pending):
            sent, note = twofactor.send_code(self.pending)
            messages.success(request, note) if sent else messages.error(request, note)
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if "resend" in request.POST:
            waited = twofactor.seconds_since_sent(self.pending)
            if waited is not None and waited < twofactor.RESEND_COOLDOWN_SECONDS:
                messages.error(request, "Please wait a moment before asking for another code.")
            else:
                sent, note = twofactor.send_code(self.pending)
                messages.success(request, note) if sent else messages.error(request, note)
            return redirect("accounts:verify")
        return super().post(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["pending_user"] = self.pending
        context["masked_email"] = twofactor.mask_email(self.pending.email)
        context["code_minutes"] = twofactor.CODE_TTL_SECONDS // 60
        return context

    def form_valid(self, form):
        attempts = self.request.session.get(twofactor.PENDING_ATTEMPTS_KEY, 0) + 1
        self.request.session[twofactor.PENDING_ATTEMPTS_KEY] = attempts

        if not twofactor.verify(self.pending, form.cleaned_data["code"]):
            AccessLog.record(
                self.request, AccessLog.Action.DENIED,
                note=f"Wrong sign-in code for {self.pending.username}",
            )
            if attempts >= twofactor.MAX_ATTEMPTS:
                twofactor.clear_code(self.pending)
                self.clear_pending()
                messages.error(self.request, "Too many incorrect codes. Please sign in again.")
                return redirect("accounts:login")
            form.add_error("code", "That code is not right, or it has expired. Check your email and try again.")
            return self.form_invalid(form)

        twofactor.clear_code(self.pending)
        next_url = self.request.session.get("pending_2fa_next") or reverse_lazy("documents:dashboard")
        self.clear_pending()
        login(self.request, self.pending, backend="django.contrib.auth.backends.ModelBackend")
        AccessLog.record(self.request, AccessLog.Action.LOGIN, note="Signed in with an emailed code")
        return redirect(str(next_url))


class SignUpView(CreateView):
    """Anyone may request an account; a super administrator decides."""

    model = User
    form_class = SignUpForm
    template_name = "accounts/signup.html"
    success_url = reverse_lazy("accounts:login")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("documents:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        AccessLog.record(
            self.request, AccessLog.Action.USER_MANAGE,
            note=f"Account request from {self.object.username}",
        )
        Notification.notify(
            recipients=User.objects.filter(role=User.Role.SUPERADMIN, is_active=True),
            actor=None,
            verb=f"{self.object.get_full_name() or self.object.username} requested an account",
        )
        messages.success(
            self.request,
            "Your request has been sent. A super administrator must approve it before you can sign in.",
        )
        return redirect("accounts:login")


class AccountRequestListView(SuperAdminRequiredMixin, ListView):
    """Pending self-registrations, for the super administrator to approve or reject."""

    template_name = "accounts/account_requests.html"
    context_object_name = "requests"

    def get_queryset(self):
        return User.objects.filter(approval_status=User.Approval.PENDING).select_related("office").order_by("date_joined")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["decided"] = User.objects.filter(
            approval_status__in=[User.Approval.REJECTED]
        ).select_related("office")[:10]
        return context


class AccountRequestDecisionView(SuperAdminRequiredMixin, View):
    """Approve or reject one request."""

    def post(self, request, pk, decision):
        applicant = get_object_or_404(User, pk=pk, approval_status=User.Approval.PENDING)
        approved = decision == "approve"

        applicant.approval_status = User.Approval.APPROVED if approved else User.Approval.REJECTED
        applicant.is_active = approved
        applicant.approved_by = request.user
        applicant.approved_at = timezone.now()
        fields = ["approval_status", "is_active", "approved_by", "approved_at"]

        if approved:
            # The role comes from the super administrator's choice, not the applicant's.
            chosen = request.POST.get("role")
            applicant.role = chosen if chosen in {User.Role.USER, User.Role.ADMIN} else User.Role.USER
            fields.append("role")
        applicant.save(update_fields=fields)

        AccessLog.record(
            request, AccessLog.Action.USER_MANAGE,
            note=(f"{'Approved' if approved else 'Rejected'} account request for {applicant.username}"
                  + (f" as {applicant.get_role_display()}" if approved else "")),
        )
        if approved:
            Notification.notify(
                recipients=[applicant], actor=request.user,
                verb="Your account request was approved. Welcome to the archive.",
            )
        messages.success(
            request,
            f"{applicant.username} "
            + (f"approved as {applicant.get_role_display()} — they can sign in now" if approved else "rejected") + ".",
        )
        return redirect("accounts:account_requests")


class ForgotPasswordView(FormView):
    """Ask who needs a reset, then email them a code."""

    template_name = "accounts/forgot_password.html"
    form_class = ForgotPasswordForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("accounts:password_change")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        identifier = form.cleaned_data["identifier"]
        user = (User.objects.filter(username__iexact=identifier).first()
                or User.objects.filter(email__iexact=identifier).exclude(email="").first())

        # Only a usable account gets a code, but the reply never says which —
        # otherwise this page becomes a way to discover who holds an account.
        if user and user.is_active and not user.is_awaiting_approval and user.email:
            sent, error = passwordreset.send_code(user)
            if sent:
                self.request.session[passwordreset.PENDING_USER_KEY] = user.pk
                self.request.session[passwordreset.PENDING_STARTED_KEY] = timezone.now().isoformat()
                self.request.session[passwordreset.PENDING_ATTEMPTS_KEY] = 0
                AccessLog.record(self.request, AccessLog.Action.USER_MANAGE,
                                 note=f"Password reset code sent for {user.username}")
            else:
                logger.error("Password reset email failed for %s: %s", user.username, error)
        elif user:
            logger.info("Password reset asked for %s but it cannot be emailed", user.username)

        messages.success(
            self.request,
            "If that account exists and has an email address, a code is on its way. "
            "Enter it below to choose a new password.",
        )
        return redirect("accounts:password_reset_confirm")


class ResetPasswordView(FormView):
    """Check the emailed code and set the new password."""

    template_name = "accounts/reset_password.html"
    form_class = ResetPasswordForm

    def dispatch(self, request, *args, **kwargs):
        self.pending = self.get_pending_user()
        return super().dispatch(request, *args, **kwargs)

    def get_pending_user(self):
        pk = self.request.session.get(passwordreset.PENDING_USER_KEY)
        started = self.request.session.get(passwordreset.PENDING_STARTED_KEY)
        if not pk or not started:
            return None
        if (timezone.now() - datetime.fromisoformat(started)).total_seconds() > passwordreset.PENDING_TIMEOUT_SECONDS:
            self.clear_pending()
            return None
        return User.objects.filter(pk=pk, is_active=True).first()

    def clear_pending(self):
        for key in (passwordreset.PENDING_USER_KEY, passwordreset.PENDING_STARTED_KEY,
                    passwordreset.PENDING_ATTEMPTS_KEY):
            self.request.session.pop(key, None)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.pending
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["masked_email"] = twofactor.mask_email(self.pending.email) if self.pending else ""
        context["code_minutes"] = passwordreset.CODE_TTL_SECONDS // 60
        return context

    def post(self, request, *args, **kwargs):
        if "resend" in request.POST:
            if self.pending:
                waited = passwordreset.seconds_since_sent(self.pending)
                if waited is not None and waited < passwordreset.RESEND_COOLDOWN_SECONDS:
                    messages.error(request, "Please wait a moment before asking for another code.")
                else:
                    passwordreset.send_code(self.pending)
                    messages.success(request, "Another code is on its way.")
            return redirect("accounts:password_reset_confirm")
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        if self.pending is None:
            messages.error(self.request, "That reset expired. Please start again.")
            return redirect("accounts:password_reset")

        attempts = self.request.session.get(passwordreset.PENDING_ATTEMPTS_KEY, 0) + 1
        self.request.session[passwordreset.PENDING_ATTEMPTS_KEY] = attempts

        if not passwordreset.verify(self.pending, form.cleaned_data["code"]):
            AccessLog.record(self.request, AccessLog.Action.DENIED,
                             note=f"Wrong password reset code for {self.pending.username}")
            if attempts >= passwordreset.MAX_ATTEMPTS:
                passwordreset.clear_code(self.pending)
                self.clear_pending()
                messages.error(self.request, "Too many incorrect codes. Please start again.")
                return redirect("accounts:password_reset")
            form.add_error("code", "That code is not right, or it has expired. Check your email and try again.")
            return self.form_invalid(form)

        self.pending.set_password(form.cleaned_data["new_password1"])
        self.pending.save(update_fields=["password"])
        passwordreset.clear_code(self.pending)
        self.clear_pending()
        AccessLog.record(self.request, AccessLog.Action.USER_MANAGE,
                         note=f"Password reset completed for {self.pending.username}")
        messages.success(self.request, "Your password has been changed. Please sign in.")
        return redirect("accounts:login")
