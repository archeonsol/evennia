from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from evennia.web.utils.auth import AuthenticationRequest, authenticate_account
from evennia.web.utils.io import release_worker_db_connections, run_on_io_thread


class CaseInsensitiveModelBackend(ModelBackend):
    """
    By default ModelBackend does case _sensitive_ username
    authentication, which isn't what is generally expected.  This
    backend supports case insensitive username authentication.

    """

    def authenticate(
        self,
        request,
        username=None,
        password=None,
        autologin=None,
        autologin_id=None,
    ):
        """
        Custom authenticate with bypass for auto-logins

        Args:
            request (Request): Request object.
            username (str, optional): Name of user to authenticate.
            password (str, optional): Password of user
            autologin (Account, optional): If given, assume this is
              an already authenticated account and bypass authentication.
        """
        if autologin_id is None and autologin is not None:
            autologin_id = getattr(autologin, "pk", autologin)
        release_worker_db_connections()
        result = run_on_io_thread(
            authenticate_account,
            AuthenticationRequest(
                username=username,
                password=password,
                autologin_id=int(autologin_id) if autologin_id is not None else None,
            ),
        )
        if result.status != "authenticated" or result.account_id is None:
            return None
        Account = get_user_model()
        try:
            account = Account.objects.get(pk=result.account_id)
        except Account.DoesNotExist:
            return None
        account.backend = "evennia.web.utils.backends.CaseInsensitiveModelBackend"
        return account
