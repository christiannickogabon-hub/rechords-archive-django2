def notifications(request):
    if not request.user.is_authenticated:
        return {}
    qs = request.user.notifications.select_related("document", "actor")
    return {
        "notification_items": qs[:8],
        "unread_notification_count": qs.filter(is_read=False).count(),
    }


def pending_account_requests(request):
    """Badge count for the super administrator's approval queue."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not user.is_super_admin:
        return {}
    from .models import User

    return {"pending_account_requests": User.objects.filter(approval_status=User.Approval.PENDING).count()}
