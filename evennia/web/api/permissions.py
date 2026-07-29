"""DRF integration for structured engine capabilities and resource policies."""

from django.conf import settings
from rest_framework import permissions

from evennia.authorization.service import has_capability


class EvenniaPermission(permissions.BasePermission):
    """
    A Django Rest Framework permission class that allows us to use Evennia's
    permission structure. Based on the action in a given view, we'll check a
    corresponding Evennia access/lock check.

    """

    # subclass this to change these permissions
    LIST_CAPABILITY = settings.REST_FRAMEWORK.get("LIST_CAPABILITY", "engine.object.examine")
    CREATE_CAPABILITY = settings.REST_FRAMEWORK.get("CREATE_CAPABILITY", "engine.object.create")
    view_operations = settings.REST_FRAMEWORK.get("VIEW_OPERATIONS", ["examine"])
    destroy_operations = settings.REST_FRAMEWORK.get("DESTROY_OPERATIONS", ["delete"])
    update_operations = settings.REST_FRAMEWORK.get("UPDATE_OPERATIONS", ["control", "edit"])

    def has_permission(self, request, view):
        """Checks for permissions

        Args:
            request (Request): The incoming request object.
            view (View): The django view we are checking permission for.

        Returns:
            bool: If permission is granted or not. If we return False here, a PermissionDenied
            error will be raised from the view.

        Notes:
            This method is a check that always happens first. If there's an object involved,
            such as with retrieve, update, or delete, then the has_object_permission method
            is called after this, assuming this returns `True`.
        """
        # Only allow authenticated users to call the API
        if not request.user.is_authenticated:
            return False
        # these actions don't support object-level permissions, so use the above definitions
        if view.action == "list":
            return has_capability(request.user, self.LIST_CAPABILITY)
        if view.action == "create":
            return has_capability(request.user, self.CREATE_CAPABILITY)
        return True  # this means we'll check object-level permissions

    @staticmethod
    def check_operations(obj, user, operations):
        """Check any named structured operation on one object.
        Args:
            obj: Object instance we're checking
            user (Account): User who we're checking permissions
            operations (list): Stable access operation names.

        Returns:
            bool: True if they have access, False if they don't
        """
        return any(obj.access(user, operation) for operation in operations)

    def has_object_permission(self, request, view, obj):
        """Checks object-level permissions after has_permission

        Args:
            request (Request): The incoming request object.
            view (View): The django view we are checking permission for.
            obj: Object we're checking object-level permissions for

        Returns:
            bool: If permission is granted or not. If we return False here, a PermissionDenied
            error will be raised from the view.

        Notes:
            This method assumes that has_permission has already returned True. We check
            equivalent Evennia permissions in the request.user to determine if they can
            complete the action.
        """
        if view.action in ("list", "retrieve"):
            # access_type is based on the examine command
            return self.check_operations(obj, request.user, self.view_operations)
        if view.action == "destroy":
            # access type based on the destroy command
            return self.check_operations(obj, request.user, self.destroy_operations)
        if view.action in ("update", "partial_update", "set_attribute"):
            # access type based on set command
            return self.check_operations(obj, request.user, self.update_operations)
