from pathlib import Path

from django.conf import settings


def _newest_asset_mtime():
    static = Path(settings.BASE_DIR) / "static"
    times = [f.stat().st_mtime for f in static.rglob("*") if f.suffix in {".css", ".js"}]
    return int(max(times)) if times else 0


def asset_version(request):
    """Cache-busting stamp for the stylesheet and scripts.

    Re-checked each request in DEBUG so edits appear without a restart; in
    production the value computed at startup is enough.
    """
    if settings.DEBUG:
        try:
            return {"asset_version": _newest_asset_mtime()}
        except OSError:
            pass
    return {"asset_version": settings.ASSET_VERSION}
