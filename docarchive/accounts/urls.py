from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("sign-in/", views.ArchiveLoginView.as_view(), name="login"),
    path("sign-up/", views.SignUpView.as_view(), name="signup"),
    path("requests/", views.AccountRequestListView.as_view(), name="account_requests"),
    path("requests/<int:pk>/<str:decision>/", views.AccountRequestDecisionView.as_view(), name="account_request_decision"),
    path("forgot-password/", views.ForgotPasswordView.as_view(), name="password_reset"),
    path("reset-password/", views.ResetPasswordView.as_view(), name="password_reset_confirm"),
    path("verify/", views.SecondFactorView.as_view(), name="verify"),
    path("sign-out/", views.ArchiveLogoutView.as_view(), name="logout"),
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("password/", views.ChangeOwnPasswordView.as_view(), name="password_change"),
    path("notifications/", views.NotificationListView.as_view(), name="notification_list"),
    path("notifications/poll/", views.notifications_poll, name="notifications_poll"),
    path("notifications/<int:pk>/open/", views.notification_open, name="notification_open"),
    path("notifications/mark-all-read/", views.notifications_mark_all_read, name="notifications_mark_all_read"),
    path("offices/", views.OfficeListView.as_view(), name="office_list"),
    path("offices/new/", views.OfficeCreateView.as_view(), name="office_create"),
    path("offices/<int:pk>/edit/", views.OfficeUpdateView.as_view(), name="office_update"),
    path("offices/<int:pk>/notify/", views.OfficeNotifyView.as_view(), name="office_notify"),
    path("users/", views.UserListView.as_view(), name="user_list"),
    path("users/new/", views.UserCreateView.as_view(), name="user_create"),
    path("users/<int:pk>/edit/", views.UserUpdateView.as_view(), name="user_update"),
    path("users/<int:pk>/message/", views.UserMessageView.as_view(), name="user_message"),
    path("users/<int:pk>/reset-password/", views.UserPasswordResetView.as_view(), name="user_password"),
]
