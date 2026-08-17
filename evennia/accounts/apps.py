"""Django application configuration for Account runtime integrations."""

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """Install owner-safe Account signal receivers once Django is ready."""

    name = "evennia.accounts"
    verbose_name = "Evennia Accounts"

    def ready(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.signals import user_logged_in

        from evennia.web.utils.auth import owner_update_last_login

        AccountDB = get_user_model()
        user_logged_in.disconnect(dispatch_uid="update_last_login")
        user_logged_in.disconnect(
            sender=AccountDB,
            dispatch_uid="update_last_login",
        )
        user_logged_in.connect(
            owner_update_last_login,
            sender=AccountDB,
            dispatch_uid="update_last_login",
        )
