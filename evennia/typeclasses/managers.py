"""
This implements the common managers that are used by the
abstract models in dbobjects.py (and which are thus shared by
all Attributes and TypedObjects).

"""

import shlex

from django.db import connection
from django.db.models import Count, ExpressionWrapper, F, FloatField, Q
from django.db.models.functions import Cast

from evennia.typeclasses.tags import Tag
from evennia.utils import idmapper
from evennia.utils.utils import (class_from_module, make_iter,
                                 variable_from_module)

__all__ = ("TypedObjectManager",)
_GA = object.__getattribute__
_Tag = None

_UNSET = object()


def _flush_attr_writes():
    """Persist pending write-behind attribute changes before a DB attribute query.

    JSONB attributes are written to the ``db_attrs`` column lazily (L1 cache,
    flushed on the maintenance tick). A search that reads ``db_attrs`` directly
    would otherwise miss attributes set since the last flush, so callers flush
    first. No-op when nothing is dirty.
    """
    from evennia.typeclasses.attributes import flush_all_dirty

    flush_all_dirty()


def _jsonb_match_pks(queryset, key, category=None, value=_UNSET):
    """Return the pks in *queryset* whose JSONB attribute document holds *key*.

    Portable fallback for backends without JSONB containment (everything
    except PostgreSQL, which uses GIN-indexed ``@>`` instead). When *value*
    is supplied the stored encoded value must equal it; otherwise only
    key-existence is tested. This iterates the candidate rows' ``db_attrs``
    documents and is therefore unindexed: it backs SQLite/MySQL dev and test
    runs, not the PostgreSQL hot path.

    Args:
        queryset (QuerySet): Rows to scan (candidate/typeclass restrictions
            should already be applied).
        key (str): Attribute key.
        category (str or None): Attribute category; ``None`` is the default
            section.
        value: Target value. Omit (leave as ``_UNSET``) to test existence only.

    Returns:
        list: Matching primary keys.
    """
    from evennia.typeclasses.jsonb_util import to_jsonb

    cat_key = "~" if category is None else str(category).lower()
    encoded = None if value is _UNSET else to_jsonb(value)
    pks = []
    for pk, doc in queryset.values_list("pk", "db_attrs"):
        data = ((doc or {}).get(cat_key) or {}).get("_d") or {}
        if key not in data:
            continue
        if value is _UNSET or data[key] == encoded:
            pks.append(pk)
    return pks


# Managers


class TypedObjectManager(idmapper.manager.SharedMemoryManager):
    """
    Common ObjectManager for all dbobjects.

    """

    def get_queryset(self):
        return super().get_queryset()

    # common methods for all typed managers. These are used
    # in other methods. Returns querysets.

    # Tag manager methods

    def get_tag(self, key=None, category=None, obj=None, tagtype=None, global_search=False):
        """
        Return Tag objects by key, by category, by object (it is
        stored on) or with a combination of those criteria.

        Args:
            key (str, optional): The Tag's key to search for
            category (str, optional): The Tag of the attribute(s)
                to search for.
            obj (Object, optional): On which object the Tag to
                search for is.
            tagtype (str, optional): One of `None` (normal tags),
                "alias" or "permission"
            global_search (bool, optional): Include all possible tags,
                not just tags on this object

        Returns:
            tag (list): The matching Tags.

        """
        global _Tag
        if not _Tag:
            from evennia.typeclasses.models import Tag as _Tag
        dbmodel = self.model.__dbclass__.__name__.lower()
        if global_search:
            # search all tags using the Tag model
            query = [("db_tagtype", tagtype), ("db_model", dbmodel)]
            if key:
                query.append(("db_key", key))
            if category:
                query.append(("db_category", category))
            else:
                query.append(("db_category", None))
            qs = _Tag.objects.filter(**dict(query))
            if obj:
                # Limit to tags actually linked to this specific object via the
                # M2M through table (Tag has no direct FK back to objects).
                through = self.model.db_tags.through
                obj_field = self.model.__name__.lower()
                linked_ids = through.objects.filter(**{"%s__id" % obj_field: obj.id}).values_list(
                    "tag_id", flat=True
                )
                qs = qs.filter(id__in=linked_ids)
            return qs
        else:
            # search only among tags stored on on this model
            query = [("tag__db_tagtype", tagtype), ("tag__db_model", dbmodel)]
            if obj:
                query.append(("%s__id" % self.model.__name__.lower(), obj.id))
            if key:
                query.append(("tag__db_key", key))
            if category:
                query.append(("tag__db_category", category))
            return Tag.objects.filter(
                pk__in=self.model.db_tags.through.objects.filter(**dict(query)).values_list(
                    "tag_id", flat=True
                )
            )

    def get_permission(self, key=None, category=None, obj=None):
        """
        Get a permission from the database.

        Args:
            key (str, optional): The permission's identifier.
            category (str, optional): The permission's category.
            obj (object, optional): The object on which this Tag is set.

        Returns:
            permission (list): Permission objects.

        """
        return self.get_tag(key=key, category=category, obj=obj, tagtype="permission")

    def get_alias(self, key=None, category=None, obj=None):
        """
        Get an alias from the database.

        Args:
            key (str, optional): The permission's identifier.
            category (str, optional): The permission's category.
            obj (object, optional): The object on which this Tag is set.

        Returns:
            alias (list): Alias objects.

        """
        return self.get_tag(key=key, category=category, obj=obj, tagtype="alias")

    # maps a tagtype to the handler attribute it is read through
    _TAGTYPE_HANDLERS = {None: "tags", "alias": "aliases", "permission": "permissions"}

    def get_tags_for_objects(self, objs, tagtype=None):
        """
        Bulk-fetch tags for many objects in a single query.

        This is the efficient counterpart to reading tags one object at a time
        (which costs one query per object *and* per category). It fetches every
        matching Tag for the whole set at once and groups them in Python.

        Args:
            objs (iterable): TypedObjects of this manager's model to fetch tags
                for. Objects whose `id` is `None` are skipped.
            tagtype (str, optional): One of `None` (normal tags), "alias" or
                "permission".

        Returns:
            dict: Mapping of `{obj_id: {category: [Tag, ...]}}`. Objects with no
                matching tags are absent from the mapping; the `None` category
                is preserved as a `None` key.

        """
        obj_ids = [obj.id for obj in objs if obj.id is not None]
        result = {}
        if not obj_ids:
            return result
        # the through-table FK and the tag's db_model are both named for the
        # concrete dbclass (e.g. "objectdb"), not the typeclass proxy
        dbmodel = self.model.__dbclass__.__name__.lower()
        through = self.model.db_tags.through
        conns = through.objects.select_related("tag").filter(
            **{
                "%s__id__in" % dbmodel: obj_ids,
                "tag__db_model": dbmodel,
                "tag__db_tagtype": tagtype,
            }
        )
        obj_id_field = "%s_id" % dbmodel
        for conn in conns:
            tag = conn.tag
            result.setdefault(getattr(conn, obj_id_field), {}).setdefault(
                tag.db_category, []
            ).append(tag)
        return result

    def prime_tag_caches(self, objs, tagtype=None):
        """
        Bulk-fetch tags for many objects and seed each object's tag handler
        cache, so subsequent `obj.tags.get(...)`/`obj.tags.all()` reads are free.

        This is the efficient path for callers that read several tag categories
        across many objects (e.g. building a snapshot of a grid): one query
        primes every handler, after which per-category reads are cache hits.
        Objects with no tags are still primed (with an empty, complete cache),
        so their reads also avoid a query.

        Args:
            objs (iterable): TypedObjects to prime. Consumed once.
            tagtype (str, optional): One of `None` (normal tags), "alias" or
                "permission"; selects which handler (`tags`/`aliases`/
                `permissions`) is primed.

        """
        objs = list(objs)
        tags_by_obj = self.get_tags_for_objects(objs, tagtype=tagtype)
        handler_name = self._TAGTYPE_HANDLERS[tagtype]
        for obj in objs:
            by_category = tags_by_obj.get(obj.id, {})
            tags = [tag for cat_tags in by_category.values() for tag in cat_tags]
            getattr(obj, handler_name)._populate_from_tags(tags)

    def get_by_tag(self, key=None, category=None, tagtype=None, **kwargs):
        """
        Return objects having tags with a given key or category or combination of the two.
        Also accepts multiple tags/category/tagtype

        Args:
            key (str or list, optional): Tag key or list of keys. Not case sensitive.
            category (str or list, optional): Tag category. Not case sensitive.
                If `key` is a list, a single category can either apply to all
                keys in that list or this must be a list matching the `key`
                list element by element. If no `key` is given, all objects with
                tags of this category are returned.
            tagtype (str, optional): 'type' of Tag, by default
                this is either `None` (a normal Tag), `alias` or
                `permission`. This always apply to all queried tags.

        Keyword Args:
            match (str): "all" (default) or "any"; determines whether the
                target object must be tagged with ALL of the provided
                tags/categories or ANY single one. ANY will perform a weighted
                sort, so objects with more tag matches will outrank those with
                fewer tag matches.

        Returns:
            objects (list): Objects with matching tag.

        Raises:
            IndexError: If `key` and `category` are both lists and `category` is shorter
                than `key`.

        """
        if key is None and category is None:
            return []

        global _Tag
        if not _Tag:
            from evennia.typeclasses.models import Tag as _Tag

        anymatch = "any" == kwargs.get("match", "all").lower().strip()

        _normalize_tag_value = lambda value: (
            str(value).strip().lower() if value is not None else None
        )

        keys = [_normalize_tag_value(val) for val in make_iter(key)] if key is not None else []
        categories = (
            [_normalize_tag_value(val) for val in make_iter(category)]
            if category is not None
            else []
        )
        n_keys = len(keys)
        n_categories = len(categories)
        unique_categories = set(categories)
        n_unique_categories = len(unique_categories)

        dbmodel = self.model.__dbclass__.__name__.lower()
        query = (
            self.filter(db_tags__db_tagtype__iexact=tagtype, db_tags__db_model__iexact=dbmodel)
            .distinct()
            .order_by("id")
        )

        if n_keys > 0:
            # keys and/or categories given
            if n_categories == 0:
                categories = [None for _ in range(n_keys)]
            elif n_categories == 1 and n_keys > 1:
                cat = categories[0]
                categories = [cat for _ in range(n_keys)]
            elif 1 < n_categories < n_keys:
                raise IndexError(
                    "get_by_tag needs a single category or a list of categories "
                    "the same length as the list of tags."
                )
            clauses = Q()
            for ikey, key in enumerate(keys):
                # ANY mode; must match any one of the given tags/categories
                clauses |= Q(db_key__iexact=key, db_category__iexact=categories[ikey])
        else:
            # only one or more categories given
            clauses = Q()
            # ANY mode; must match any one of them
            for category in unique_categories:
                clauses |= Q(db_category__iexact=category)

        tags = _Tag.objects.filter(clauses)
        query = query.filter(db_tags__in=tags).annotate(
            matches=Count("db_tags__pk", filter=Q(db_tags__in=tags), distinct=True)
        )

        if anymatch:
            # ANY: Match any single tag, ordered by weight
            query = query.order_by("-matches")
        else:
            # Default ALL: Match all of the tags and optionally more
            n_req_tags = n_keys if n_keys > 0 else n_unique_categories
            query = query.filter(matches__gte=n_req_tags)

        return query

    def get_by_permission(self, key=None, category=None):
        """
        Return objects having permissions with a given key or category or
        combination of the two.

        Args:
            key (str, optional): Permissions key. Not case sensitive.
            category (str, optional): Permission category. Not case sensitive.
        Returns:
            objects (list): Objects with matching permission.
        """
        return self.get_by_tag(key=key, category=category, tagtype="permission")

    def get_by_alias(self, key=None, category=None):
        """
        Return objects having aliases with a given key or category or
        combination of the two.

        Args:
            key (str, optional): Alias key. Not case sensitive.
            category (str, optional): Alias category. Not case sensitive.
        Returns:
            objects (list): Objects with matching alias.
        """
        return self.get_by_tag(key=key, category=category, tagtype="alias")

    def create_tag(self, key=None, category=None, data=None, tagtype=None):
        """
        Create a new Tag of the base type associated with this
        object.  This makes sure to create case-insensitive tags.
        If the exact same tag configuration (key+category+tagtype+dbmodel)
        exists on the model, a new tag will not be created, but an old
        one returned.


        Args:
            key (str, optional): Tag key. Not case sensitive.
            category (str, optional): Tag category. Not case sensitive.
            data (str, optional): Extra information about the tag.
            tagtype (str or None, optional): 'type' of Tag, by default
                this is either `None` (a normal Tag), `alias` or
                `permission`.
        Notes:
            The `data` field is not part of the uniqueness of the tag:
            Setting `data` on an existing tag will overwrite the old
            data field. It is intended only as a way to carry
            information about the tag (like a help text), not to carry
            any information about the tagged objects themselves.

        """
        data = str(data) if data is not None else None
        # try to get old tag

        dbmodel = self.model.__dbclass__.__name__.lower()
        tag = self.get_tag(key=key, category=category, tagtype=tagtype, global_search=True)
        if tag and data is not None:
            # get tag from list returned by get_tag
            tag = tag[0]
            # overload data on tag
            tag.db_data = data
            tag.save()
        elif not tag:
            # create a new tag
            global _Tag
            if not _Tag:
                from evennia.typeclasses.models import Tag as _Tag
            tag = _Tag.objects.create(
                db_key=key.strip().lower() if key is not None else None,
                db_category=category.strip().lower() if category and key is not None else None,
                db_data=data,
                db_model=dbmodel,
                db_tagtype=tagtype.strip().lower() if tagtype is not None else None,
            )
            tag.save()
        return make_iter(tag)[0]

    def dbref(self, dbref, reqhash=True):
        """
        Determing if input is a valid dbref.

        Args:
            dbref (str or int): A possible dbref.
            reqhash (bool, optional): If the "#" is required for this
                to be considered a valid hash.

        Returns:
            dbref (int or None): The integer part of the dbref.

        Notes:
            Valid forms of dbref (database reference number) are
            either a string '#N' or an integer N.

        """
        if reqhash and not (isinstance(dbref, str) and dbref.startswith("#")):
            return None
        if isinstance(dbref, str):
            dbref = dbref.lstrip("#")
        try:
            if int(dbref) < 0:
                return None
        except Exception:
            return None
        return dbref

    def get_id(self, dbref):
        """
        Find object with given dbref.

        Args:
            dbref (str or int): The id to search for.

        Returns:
            object (TypedObject): The matched object.

        """
        dbref = self.dbref(dbref, reqhash=False)
        try:
            return self.get(id=dbref)
        except self.model.DoesNotExist:
            pass
        return None

    def dbref_search(self, dbref):
        """
        Alias to get_id.

        Args:
            dbref (str or int): The id to search for.

        Returns:
            Queryset: Queryset with 0 or 1 match.

        """
        dbref = self.dbref(dbref, reqhash=False)
        if dbref:
            return self.filter(id=dbref)
        return self.none()

    search_dbref = dbref_search  # alias

    def get_dbref_range(self, min_dbref=None, max_dbref=None):
        """
        Get objects within a certain range of dbrefs.

        Args:
            min_dbref (int): Start of dbref range.
            max_dbref (int): End of dbref range (inclusive)

        Returns:
            objects (list): TypedObjects with dbrefs within
                the given dbref ranges.

        """
        retval = super().all()
        if min_dbref is not None:
            retval = retval.filter(id__gte=self.dbref(min_dbref, reqhash=False))
        if max_dbref is not None:
            retval = retval.filter(id__lte=self.dbref(max_dbref, reqhash=False))
        return retval

    def get_typeclass_totals(self, *args, **kwargs) -> object:
        """
        Returns a queryset of typeclass composition statistics.

        Returns:
            qs (Queryset): A queryset of dicts containing the typeclass (name),
                the count of objects with that typeclass and a float representing
                the percentage of objects associated with the typeclass.

        """
        return (
            self.values("db_typeclass_path")
            .distinct()
            .annotate(
                # Get count of how many objects for each typeclass exist
                count=Count("db_typeclass_path")
            )
            .annotate(
                # Rename db_typeclass_path field to something more human
                typeclass=F("db_typeclass_path"),
                # Calculate this class' percentage of total composition
                percent=ExpressionWrapper(
                    ((F("count") / float(self.count())) * 100.0),
                    output_field=FloatField(),
                ),
            )
            .values("typeclass", "count", "percent")
        )

    def object_totals(self):
        """
        Get info about database statistics.

        Returns:
            census (dict): A dictionary `{typeclass_path: number, ...}` with
                all the typeclasses active in-game as well as the number
                of such objects defined (i.e. the number of database
                object having that typeclass set on themselves).

        """
        stats = self.get_typeclass_totals().order_by("typeclass")
        return {x.get("typeclass"): x.get("count") for x in stats}

    def typeclass_search(self, typeclass, include_children=False, include_parents=False):
        """
        Searches through all objects returning those which are of the
        specified typeclass.

        Args:
            typeclass (str or class): A typeclass class or a python path to a typeclass.
            include_children (bool, optional): Return objects with
                given typeclass *and* all children inheriting from this
                typeclass. Mutually exclusive to `include_parents`.
            include_parents (bool, optional): Return objects with
                given typeclass *and* all parents to this typeclass.
                Mutually exclusive to `include_children`.

        Returns:
            objects (list): The objects found with the given typeclasses.

        Raises:
            ImportError: If the provided `typeclass` is not a valid typeclass or the
                path to an existing typeclass.

        """
        if not callable(typeclass):
            typeclass = class_from_module(typeclass)

        if include_children:
            query = typeclass.objects.all_family()
        else:
            query = typeclass.objects.all()

        if include_parents:
            parents = typeclass.__mro__
            if parents:
                parent_queries = []
                for parent in (parent for parent in parents if hasattr(parent, "path")):
                    parent_queries.append(super().filter(db_typeclass_path__exact=parent.path))
                query = query.union(*parent_queries)

        return query


class TypeclassManager(TypedObjectManager):
    """
    Manager for the typeclasses. The main purpose of this manager is
    to limit database queries to the given typeclass despite all
    typeclasses technically being defined in the same core database
    model.

    """

    # object-manager methods
    def smart_search(self, query):
        """
        Search by supplying a string with optional extra search criteria to aid the query.

        Args:
            query (str): A search criteria that accepts extra search criteria on the following
            forms:

                [key|alias|#dbref...]
                [tag==<tagstr>[:category]...]
                [attr==<key>:<value>:category...]

            All three can be combined in the same query, separated by spaces.

        Returns:
            matches (queryset): A queryset result matching all queries exactly. If wanting to use
                spaces or ==, != in tags or attributes, enclose them in quotes.

        Example:
            house = smart_search("key=foo alias=bar tag=house:building tag=magic attr=color:red")

        Note:
            The flexibility of this method is limited by the input line format. Tag/attribute
            matching only works for matching primitives.  For even more complex queries, such as
            'in' operations or object field matching, use the full django query language.

        """
        # shlex splits by spaces unless escaped by quotes
        querysplit = shlex.split(query)
        queries, plustags, plusattrs, negtags, negattrs = [], [], [], [], []
        for ipart, part in enumerate(querysplit):
            key, rest = part, ""
            if ":" in part:
                key, rest = part.split(":", 1)
            # tags are on the form tag or tag:category
            if key.startswith("tag=="):
                plustags.append((key[5:], rest))
                continue
            elif key.startswith("tag!="):
                negtags.append((key[5:], rest))
                continue
            # attrs are on the form attr:value or attr:value:category
            elif rest:
                value, category = rest, ""
                if ":" in rest:
                    value, category = rest.split(":", 1)
                if key.startswith("attr=="):
                    plusattrs.append((key[7:], value, category))
                    continue
                elif key.startswith("attr!="):
                    negattrs.append((key[7:], value, category))
                    continue
            # if we get here, we are entering a key search criterion which
            # we assume is one word.
            queries.append(part)
        # build query from components
        key_query = " ".join(queries)

        # Start with the full typeclass queryset, then narrow down.
        qs = self.all()

        if key_query:
            dbref = self.dbref(key_query)
            if dbref:
                qs = qs.filter(id=dbref)
            else:
                # Match by key or alias tag.
                alias_ids = self.model.db_tags.through.objects.filter(
                    tag__db_key__iexact=key_query,
                    tag__db_tagtype="alias",
                ).values_list("%s_id" % self.model.__name__.lower(), flat=True)
                qs = qs.filter(Q(db_key__iexact=key_query) | Q(id__in=alias_ids))

        for tagkey, tagcat in plustags:
            qs = qs.filter(
                db_tags__db_key__iexact=tagkey,
                db_tags__db_category__iexact=tagcat if tagcat else None,
                db_tags__db_tagtype=None,
            )
        for tagkey, tagcat in negtags:
            qs = qs.exclude(
                db_tags__db_key__iexact=tagkey,
                db_tags__db_category__iexact=tagcat if tagcat else None,
                db_tags__db_tagtype=None,
            )

        if plusattrs or negattrs:
            _flush_attr_writes()
        if connection.vendor == "postgresql":
            from evennia.typeclasses.jsonb_util import to_jsonb

            for attrkey, attrval, attrcat in plusattrs:
                cat_key = "~" if not attrcat else attrcat.lower()
                qs = qs.filter(db_attrs__contains={cat_key: {"_d": {attrkey: to_jsonb(attrval)}}})
            for attrkey, attrval, attrcat in negattrs:
                cat_key = "~" if not attrcat else attrcat.lower()
                qs = qs.exclude(db_attrs__contains={cat_key: {"_d": {attrkey: to_jsonb(attrval)}}})
        else:
            for attrkey, attrval, attrcat in plusattrs:
                qs = qs.filter(pk__in=_jsonb_match_pks(qs, attrkey, attrcat or None, attrval))
            for attrkey, attrval, attrcat in negattrs:
                qs = qs.exclude(pk__in=_jsonb_match_pks(qs, attrkey, attrcat or None, attrval))

        return qs.distinct()

    def get(self, *args, **kwargs):
        """
        Overload the standard get. This will limit itself to only
        return the current typeclass.

        Args:
            args (any): These are passed on as arguments to the default
                django get method.
        Keyword Args:
            kwargs (any): These are passed on as normal arguments
                to the default django get method
        Returns:
            object (object): The object found.

        Raises:
            ObjectNotFound: The exact name of this exception depends
                on the model base used.

        """
        kwargs.update({"db_typeclass_path": self.model.path})
        return super().get(**kwargs)

    def filter(self, *args, **kwargs):
        """
        Overload of the standard filter function. This filter will
        limit itself to only the current typeclass.

        Args:
            args (any): These are passed on as arguments to the default
                django filter method.
        Keyword Args:
            kwargs (any): These are passed on as normal arguments
                to the default django filter method.
        Returns:
            objects (queryset): The objects found.

        """
        kwargs.update({"db_typeclass_path": self.model.path})
        return super().filter(*args, **kwargs)

    def all(self):
        """
        Overload method to return all matches, filtering for typeclass.

        Returns:
            objects (queryset): The objects found.

        """
        return super().all().filter(db_typeclass_path=self.model.path)

    def first(self):
        """
        Overload method to return first match, filtering for typeclass.

        Returns:
            object (object): The object found.

        Raises:
            ObjectNotFound: The exact name of this exception depends
                on the model base used.

        """
        return super().filter(db_typeclass_path=self.model.path).first()

    def last(self):
        """
        Overload method to return last match, filtering for typeclass.

        Returns:
            object (object): The object found.

        Raises:
            ObjectNotFound: The exact name of this exception depends
                on the model base used.

        """
        return super().filter(db_typeclass_path=self.model.path).last()

    def count(self):
        """
        Overload method to return number of matches, filtering for typeclass.

        Returns:
            integer : Number of objects found.

        """
        return super().filter(db_typeclass_path=self.model.path).count()

    def annotate(self, *args, **kwargs):
        """
        Overload annotate method to filter on typeclass before annotating.
        Args:
            *args (any): Positional arguments passed along to queryset annotate method.
            **kwargs (any): Keyword arguments passed along to queryset annotate method.

        Returns:
            Annotated queryset.
        """
        return super().filter(db_typeclass_path=self.model.path).annotate(*args, **kwargs)

    def values(self, *args, **kwargs):
        """
        Overload values method to filter on typeclass first.
        Args:
            *args (any): Positional arguments passed along to values method.
            **kwargs (any): Keyword arguments passed along to values method.

        Returns:
            Queryset of values dictionaries, just filtered by typeclass first.
        """
        return super().filter(db_typeclass_path=self.model.path).values(*args, **kwargs)

    def values_list(self, *args, **kwargs):
        """
        Overload values method to filter on typeclass first.
        Args:
            *args (any): Positional arguments passed along to values_list method.
            **kwargs (any): Keyword arguments passed along to values_list method.

        Returns:
            Queryset of value_list tuples, just filtered by typeclass first.
        """
        return super().filter(db_typeclass_path=self.model.path).values_list(*args, **kwargs)

    # Per-class cache: {cls: [subclass, ...]}  Cleared when new subclasses are registered.
    _subclass_cache: dict = {}

    def _get_subclasses(self, cls):
        """
        Recursively get all subclasses of *cls*, with results cached on the
        manager class to avoid repeated MRO walks on every filter_family call.

        Args:
            cls (class): A class to get subclasses from.
        """
        cached = self.__class__._subclass_cache.get(cls)
        if cached is not None:
            return cached
        all_subclasses = cls.__subclasses__()
        for subclass in list(all_subclasses):
            all_subclasses.extend(self._get_subclasses(subclass))
        self.__class__._subclass_cache[cls] = all_subclasses
        return all_subclasses

    def get_family(self, *args, **kwargs):
        """
        Variation of get that not only returns the current typeclass
        but also all subclasses of that typeclass.

        Keyword Args:
            kwargs (any): These are passed on as normal arguments
                to the default django get method.
        Returns:
            objects (list): The objects found.

        Raises:
            ObjectNotFound: The exact name of this exception depends
                on the model base used.

        """
        paths = [self.model.path] + [
            "%s.%s" % (cls.__module__, cls.__name__) for cls in self._get_subclasses(self.model)
        ]
        kwargs.update({"db_typeclass_path__in": paths})
        return super().get(*args, **kwargs)

    def filter_family(self, *args, **kwargs):
        """
        Variation of filter that allows results both from typeclass
        and from subclasses of typeclass

        Args:
            args (any): These are passed on as arguments to the default
                django filter method.
        Keyword Args:
            kwargs (any): These are passed on as normal arguments
                to the default django filter method.
        Returns:
            objects (list): The objects found.

        """
        # query, including all subclasses
        paths = [self.model.path] + [
            "%s.%s" % (cls.__module__, cls.__name__) for cls in self._get_subclasses(self.model)
        ]
        kwargs.update({"db_typeclass_path__in": paths})
        return super().filter(*args, **kwargs)

    def all_family(self):
        """
        Return all matches, allowing matches from all subclasses of
        the typeclass.

        Returns:
            objects (list): The objects found.

        """
        paths = [self.model.path] + [
            "%s.%s" % (cls.__module__, cls.__name__) for cls in self._get_subclasses(self.model)
        ]
        return super().all().filter(db_typeclass_path__in=paths)
