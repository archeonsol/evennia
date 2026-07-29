"""
Serializers in the Django Rest Framework are similar to Forms in normal django.
They're used for transmitting and validating data, both going to clients and
coming to the server. However, where forms often contained presentation logic,
such as specifying widgets to use for selection, serializers typically leave
those decisions in the hands of clients, and are more focused on converting
data from the server to JSON (serialization) for a response, and validating
and converting JSON data sent from clients to our enpoints into python objects,
often django model instances, that we can use (deserialization).

"""

from rest_framework import serializers

from evennia.accounts.accounts import DefaultAccount
from evennia.help.models import HelpEntry
from evennia.objects.objects import DefaultObject
from evennia.scripts.models import ScriptDB
from evennia.typeclasses.tags import Tag


class AttributeSerializer(serializers.Serializer):
    """
    Serialize IAttribute objects (JsonbAttribute or InMemoryAttribute).

    db_key / db_category / db_attrtype are exposed without source= so that
    validated_data keys match what views.set_attribute reads.  db_value is
    write-only (input only); value_display is the read path for output.
    """

    db_key = serializers.CharField()
    db_category = serializers.CharField(allow_null=True, required=False, default=None)
    db_attrtype = serializers.CharField(allow_null=True, required=False, default=None)
    # CharField (not JSONField) so form-encoded scalar values are accepted as-is;
    # a JSONField would reject a bare string like "test_value" as invalid JSON.
    db_value = serializers.CharField(required=False, allow_null=True, write_only=True)
    value_display = serializers.SerializerMethodField()

    @staticmethod
    def get_value_display(obj) -> str:
        strvalue = getattr(obj, "strvalue", None) or getattr(obj, "db_strvalue", None)
        if strvalue:
            return strvalue
        return str(getattr(obj, "value", ""))


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["db_key", "db_category", "db_data", "db_tagtype"]


class SimpleObjectDBSerializer(serializers.ModelSerializer):
    class Meta:
        model = DefaultObject
        fields = ["id", "db_key"]


class TypeclassSerializerMixin:
    """
    Mixin that contains types shared by typeclasses. A note about tags,
    aliases, and capabilities. You might note that the methods and fields are
    defined here, but they're included explicitly in each child class. What
    gives? It's a DRF error: serializer method fields which are inherited do
    not resolve correctly in child classes, and as of this current version
    (3.11) you must have them in the child classes explicitly to avoid field
    errors. Similarly, the child classes must contain the attribute serializer
    explicitly to not have them render PK-related fields.

    """

    shared_fields = [
        "id",
        "db_key",
        "attributes",
        "db_typeclass_path",
        "aliases",
        "tags",
        "capabilities",
    ]

    @staticmethod
    def get_tags(obj):
        """
        Serializes tags from the object's Tagshandler
        Args:
            obj: Typeclassed object being serialized

        Returns:
            List of TagSerializer data
        """
        return TagSerializer(obj.tags.get(return_tagobj=True, return_list=True), many=True).data

    @staticmethod
    def get_aliases(obj):
        """
        Serializes tags from the object's Aliashandler
        Args:
            obj: Typeclassed object being serialized

        Returns:
            List of TagSerializer data
        """
        return TagSerializer(obj.aliases.get(return_tagobj=True, return_list=True), many=True).data

    @staticmethod
    def get_capabilities(obj):
        """Serialize explicit capability scopes without grant identifiers."""

        from evennia.authorization.storage import load_grants

        grants = load_grants(obj)
        return [
            {
                "capability": capability,
                "scopes": [f"{scope.kind}:{scope.key}" for scope in scopes],
            }
            for capability, scopes in sorted(grants.by_capability.items())
        ]

    @staticmethod
    def get_attributes(obj):
        """
        Serializes attributes from the object's AttributeHandler
        Args:
            obj: Typeclassed object being serialized

        Returns:
            List of AttributeSerializer data
        """
        return AttributeSerializer(obj.attributes.all(), many=True).data

    @staticmethod
    def get_nicks(obj):
        """
        Serializes attributes from the object's NicksHandler
        Args:
            obj: Typeclassed object being serialized

        Returns:
            List of AttributeSerializer data
        """
        return AttributeSerializer(obj.nicks.all(), many=True).data


class TypeclassListSerializerMixin:
    """
    Shortened serializer for list views.

    """

    shared_fields = [
        "id",
        "db_key",
        "db_typeclass_path",
    ]


class ObjectDBSerializer(TypeclassSerializerMixin, serializers.ModelSerializer):
    """
    Serializing Objects.

    """

    attributes = serializers.SerializerMethodField()
    nicks = serializers.SerializerMethodField()
    contents = serializers.SerializerMethodField()
    exits = serializers.SerializerMethodField()
    tags = serializers.SerializerMethodField()
    aliases = serializers.SerializerMethodField()
    capabilities = serializers.SerializerMethodField()

    class Meta:
        model = DefaultObject
        fields = [
            "db_location",
            "db_home",
            "contents",
            "exits",
            "nicks",
        ] + TypeclassSerializerMixin.shared_fields
        read_only_fields = ["id"]

    @staticmethod
    def get_exits(obj):
        """
        Gets exits for the object
        Args:
            obj: Object being serialized

        Returns:
            List of data from SimpleObjectDBSerializer
        """
        exits = [ob for ob in obj.contents if ob.destination]
        return SimpleObjectDBSerializer(exits, many=True).data

    @staticmethod
    def get_contents(obj):
        """
        Gets non-exits for the object
        Args:
            obj: Object being serialized

        Returns:
            List of data from SimpleObjectDBSerializer
        """
        non_exits = [ob for ob in obj.contents if not ob.destination]
        return SimpleObjectDBSerializer(non_exits, many=True).data


class ObjectListSerializer(TypeclassListSerializerMixin, serializers.ModelSerializer):
    """
    Shortened representation for listings.]

    """

    class Meta:
        model = DefaultObject
        fields = [
            "db_location",
            "db_home",
        ] + TypeclassListSerializerMixin.shared_fields
        read_only_fields = ["id"]


class AccountSerializer(TypeclassSerializerMixin, serializers.ModelSerializer):
    """
    This uses the DefaultAccount object to have access to the sessions property

    """

    attributes = serializers.SerializerMethodField()
    nicks = serializers.SerializerMethodField()
    db_key = serializers.CharField(required=False)
    session_ids = serializers.SerializerMethodField()
    tags = serializers.SerializerMethodField()
    aliases = serializers.SerializerMethodField()
    capabilities = serializers.SerializerMethodField()

    @staticmethod
    def get_session_ids(obj):
        """
        Gets a list of session IDs connected to this Account
        Args:
            obj (DefaultAccount): Account we're grabbing sessions from

        Returns:
            List of session IDs
        """
        return [sess.sessid for sess in obj.sessions.all() if hasattr(sess, "sessid")]

    class Meta:
        model = DefaultAccount
        fields = [
            "username",
            "session_ids",
            "nicks",
        ] + TypeclassSerializerMixin.shared_fields
        read_only_fields = ["id"]


class AccountListSerializer(TypeclassListSerializerMixin, serializers.ModelSerializer):
    """
    A shortened form for listing.

    """

    class Meta:
        model = DefaultAccount
        fields = ["username"] + [
            fi for fi in TypeclassListSerializerMixin.shared_fields if fi != "db_key"
        ]
        read_only_fields = ["id"]


class ScriptDBSerializer(TypeclassSerializerMixin, serializers.ModelSerializer):
    """
    Serializing Account.

    """

    attributes = serializers.SerializerMethodField()
    tags = serializers.SerializerMethodField()
    aliases = serializers.SerializerMethodField()
    capabilities = serializers.SerializerMethodField()

    class Meta:
        model = ScriptDB
        fields = [
            "db_persistent",
        ] + TypeclassSerializerMixin.shared_fields
        read_only_fields = ["id"]


class ScriptListSerializer(TypeclassListSerializerMixin, serializers.ModelSerializer):
    """
    Shortened form for listing.

    """

    class Meta:
        model = ScriptDB
        fields = [
            "db_persistent",
        ] + TypeclassListSerializerMixin.shared_fields
        read_only_fields = ["id"]


class HelpSerializer(TypeclassSerializerMixin, serializers.ModelSerializer):
    """
    Serializers Help entries (not a typeclass).

    """

    tags = serializers.SerializerMethodField()
    aliases = serializers.SerializerMethodField()

    class Meta:
        model = HelpEntry
        fields = [
            "id",
            "db_key",
            "db_help_category",
            "db_entrytext",
            "db_date_created",
            "tags",
            "aliases",
        ]
        read_only_fields = ["id"]


class HelpListSerializer(TypeclassListSerializerMixin, serializers.ModelSerializer):
    """
    Shortened form for listings.

    """

    class Meta:
        model = HelpEntry
        fields = [
            "id",
            "db_key",
            "db_help_category",
            "db_date_created",
        ]
        read_only_fields = ["id"]
