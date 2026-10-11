from django.test import TestCase
from django.urls import reverse

from documents.models import AccessLog

from .models import User


class AdminPasswordResetTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("admin2", password="pw-for-tests-123", role=User.Role.ADMIN)
        self.target = User.objects.create_user("clerk8", password="pw-for-tests-123")
        self.client.force_login(self.admin)

    def reset(self, password, **extra):
        return self.client.post(
            reverse("accounts:user_password", args=[self.target.pk]),
            {"password1": password, "password2": password, **extra},
        )

    def test_weak_password_is_rejected_without_the_checkbox(self):
        response = self.reset("123")
        self.assertEqual(response.status_code, 200)
        self.target.refresh_from_db()
        self.assertFalse(self.target.check_password("123"))

    def test_checkbox_allows_a_weak_password_and_is_logged(self):
        response = self.reset("123", bypass_validation="on")
        self.assertEqual(response.status_code, 302)
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password("123"))
        self.assertTrue(AccessLog.objects.filter(note__contains="bypassed").exists())

    def test_strong_password_still_works_without_the_checkbox(self):
        response = self.reset("Str0ng-archive-pw")
        self.assertEqual(response.status_code, 302)
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password("Str0ng-archive-pw"))
        self.assertFalse(AccessLog.objects.filter(note__contains="bypassed").exists())


class DisabledAccountLoginTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("clerk9", password="pw-for-tests-123", is_active=False)

    def sign_in(self, password):
        return self.client.post(reverse("accounts:login"), {"username": "clerk9", "password": password})

    def test_disabled_account_with_correct_password_sees_warning(self):
        response = self.sign_in("pw-for-tests-123")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This account has been disabled.")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertTrue(AccessLog.objects.filter(action=AccessLog.Action.DENIED, note__contains="clerk9").exists())

    def test_wrong_password_does_not_reveal_disabled_state(self):
        response = self.sign_in("wrong-password")
        self.assertNotContains(response, "disabled")
        self.assertContains(response, "Please enter a correct username and password")
        self.assertFalse(AccessLog.objects.filter(action=AccessLog.Action.DENIED).exists())

    def test_active_account_still_signs_in(self):
        self.user.is_active = True
        self.user.save()
        response = self.sign_in("pw-for-tests-123")
        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)


class UserMessageTests(TestCase):
    def setUp(self):
        from datetime import date

        from documents.models import Document, DocumentType

        self.admin = User.objects.create_user(
            "admin3", password="pw-for-tests-123", role=User.Role.ADMIN
        )
        self.clerk = User.objects.create_user("clerk9", password="pw-for-tests-123")
        self.document = Document.objects.create(
            doc_type=DocumentType.MEMORANDUM, series_year=2026, subject="Filed already",
            date_issued=date(2026, 5, 2), created_by=self.admin,
        )
        self.client.force_login(self.admin)

    def send(self, to, **extra):
        return self.client.post(
            reverse("accounts:user_message", args=[to.pk]),
            {"subject": "Correction needed", "message": "Page 2 shows the wrong date.", **extra},
        )

    def test_message_reaches_the_recipient_as_a_notification(self):
        from documents.models import Notification

        response = self.send(self.clerk)
        self.assertEqual(response.status_code, 302)
        note = Notification.objects.get(recipient=self.clerk)
        self.assertEqual(note.actor, self.admin)
        self.assertIn("Correction needed", note.verb)

    def test_message_can_point_at_a_filed_document_without_uploading(self):
        from documents.models import Notification

        self.send(self.clerk, document=self.document.pk)
        note = Notification.objects.get(recipient=self.clerk)
        self.assertEqual(note.document, self.document)
        self.assertIn(self.document.control_number, note.verb)

    def test_sender_cannot_message_themselves(self):
        from documents.models import Notification

        self.send(self.admin)
        self.assertEqual(Notification.objects.count(), 0)

    def test_disabled_account_is_not_messaged(self):
        from documents.models import Notification

        self.clerk.is_active = False
        self.clerk.save(update_fields=["is_active"])
        self.send(self.clerk)
        self.assertEqual(Notification.objects.count(), 0)

    def test_staff_users_cannot_open_the_message_page(self):
        self.client.force_login(self.clerk)
        response = self.client.get(reverse("accounts:user_message", args=[self.admin.pk]))
        self.assertEqual(response.status_code, 403)


class ContactNumberTests(TestCase):
    """Every contact field: 11 digits, or 12 with the country code. No more."""

    def field(self):
        from .forms import PHContactNumberField

        return PHContactNumberField(required=False)

    def assertDenied(self, value, code=None):
        from django import forms as django_forms

        with self.assertRaises(django_forms.ValidationError, msg=f"{value!r} was accepted") as caught:
            self.field().clean(value)
        if code:
            self.assertEqual(caught.exception.error_list[0].code, code)

    def test_eleven_and_twelve_digit_numbers_are_accepted(self):
        for value in ("0917 123 4567", "09171234567", "+63 917 123 4567", "639171234567"):
            self.assertEqual(self.field().clean(value), "+639171234567", value)

    def test_more_than_eleven_digits_is_denied(self):
        self.assertDenied("091712345678", "too_long")
        self.assertDenied("0917123456789", "too_long")

    def test_more_than_twelve_digits_with_the_country_code_is_denied(self):
        self.assertDenied("6391712345678", "too_long")
        self.assertDenied("+63 917 123 45678", "too_long")

    def test_short_numbers_are_denied(self):
        self.assertDenied("091712345", "too_short")
        self.assertDenied("0917", "too_short")

    def test_a_number_that_is_not_a_mobile_is_denied(self):
        self.assertDenied("0817 123 4567", "invalid")

    def test_blank_is_allowed(self):
        self.assertEqual(self.field().clean(""), "")

    def test_the_input_is_capped_in_the_browser_too(self):
        field = self.field()
        self.assertEqual(int(field.widget.attrs["maxlength"]), 17)
        self.assertEqual(field.max_length, 17)

    def test_every_account_form_uses_the_checked_field(self):
        from .forms import PHContactNumberField, ProfileForm, SignUpForm, UserCreateForm, UserUpdateForm

        for form_class in (ProfileForm, SignUpForm, UserCreateForm, UserUpdateForm):
            field = form_class().fields.get("contact_number")
            self.assertIsInstance(field, PHContactNumberField, form_class.__name__)

    def test_a_long_number_is_refused_when_the_profile_is_saved(self):
        user = User.objects.create_user("clerk20", password="pw-for-tests-123")
        self.client.force_login(user)
        response = self.client.post(reverse("accounts:profile"), {
            "first_name": "Juan", "last_name": "Dela Cruz",
            "email": "juan@example.com", "contact_number": "0917123456789",
        })
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.contact_number, "")
