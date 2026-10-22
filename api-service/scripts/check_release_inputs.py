"""Safe non-billable release preflight; never prints settings or connection errors."""

import json


def main():
    from app.audio_store import check_volume
    from app.config import get_settings
    from app.database import check_schema_version
    from app.local_cache import wrapping_key

    try:
        settings = get_settings()
        if settings.app_env != "production":
            raise RuntimeError("Production profile required")
        check_volume(settings)
        check_schema_version()
        if settings.browser_recovery_enabled:
            wrapping_key(settings, settings.local_cache_kek_id)
    except Exception:
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "detail": "Configuration, schema, mount or wrapping-key preflight failed; inspect restricted deployment inputs",
                }
            )
        )
        raise SystemExit(1) from None
    print(
        json.dumps(
            {
                "status": "PASS",
                "profile": "production",
                "schema": "0002_production",
                "note": "Non-billable preflight only; real identity/provider/load/restore gates remain required",
            }
        )
    )


if __name__ == "__main__":
    main()
