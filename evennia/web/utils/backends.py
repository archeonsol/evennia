from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from evennia.utils import logger
from evennia.web.utils.auth import AuthenticationRequest, check_credentials, rehash_password
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
        auth_request = AuthenticationRequest(
            username=username,
            password=password,
            autologin_id=int(autologin_id) if autologin_id is not None else None,
        )
        # Verified on this thread. The check reads three plain columns and
        # compares two strings, so it needs no owner -- and signing in must
        # keep working while the game server is down, or degraded mode helps
        # only sessions that were already open.
        result = check_credentials(auth_request)
        if result.status != "authenticated" or result.account_id is None:
            return None
        if result.needs_rehash and password:
            self._upgrade_hash(result.account_id, password)
        Account = get_user_model()
        try:
            account = Account.objects.get(pk=result.account_id)
        except Account.DoesNotExist:
            return None
        account.backend = "evennia.web.utils.backends.CaseInsensitiveModelBackend"
        return account

    def _upgrade_hash(self, account_id, password):
        """Rewrite a superseded password hash, if the owner is reachable.

        Opportunistic on purpose. The write needs the owner because it saves an
        identity-cached model, but a password that is valid under an older
        hasher is a missed optimization rather than a security failure. It must
        never be the reason a sign-in fails.
        """
        try:
            release_worker_db_connections()
            run_on_io_thread(rehash_password, int(account_id), password)
        except Exception as err:  # noqa: BLE001 - never block a valid sign-in
            logger.log_info(
                f"Password hash upgrade deferred for Account #{account_id}: {type(err).__name__}"
            )
