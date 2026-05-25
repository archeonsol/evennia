"""
Redis-backed per-room scene index for ``msg_contents`` recipient gathering.

Tracks character (and optional creature) dbrefs per room. PostgreSQL location
remains authoritative; rebuild on start or cache miss.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Set

from django.conf import settings

from evennia.utils import logger

_CACHE_VERSION = "v1"
_KEY_PREFIX = "roomscene:%s:" % _CACHE_VERSION


def _enabled() -> bool:
    return bool(getattr(settings, "ROOM_SCENE_INDEX_ENABLED", True))


def _redis_alias() -> str:
    return getattr(settings, "ROOM_SCENE_INDEX_REDIS_ALIAS", "default")


def _typeclass_paths() -> frozenset:
    return frozenset(
        getattr(
            settings,
            "ROOM_SCENE_INDEX_TYPECLASS_PATHS",
            (
                "typeclasses.characters.base.Character",
                "typeclasses.characters.Character",
                "typeclasses.creatures.Creature",
            ),
        )
    )


def _redis_conn():
    try:
        from django_redis import get_redis_connection

        return get_redis_connection(_redis_alias())
    except Exception as exc:
        logger.log_warn("room_scene_index: redis unavailable: %s" % exc)
        return None


def _room_key(room_id: int) -> str:
    return _KEY_PREFIX + str(int(room_id))


def _is_indexed(obj) -> bool:
    if not obj or not getattr(obj, "id", None):
        return False
    path = getattr(obj, "db_typeclass_path", None) or ""
    return path in _typeclass_paths()


def _member_id(obj) -> Optional[int]:
    if not _is_indexed(obj):
        return None
    return int(obj.id)


def bump_room_generation(room) -> None:
    if not room or not hasattr(room, "ndb"):
        return
    try:
        room.ndb._scene_generation = int(getattr(room.ndb, "_scene_generation", 0) or 0) + 1
    except Exception:
        pass


def add_to_room(room, obj) -> None:
    if not _enabled() or not room or not getattr(room, "id", None):
        return
    mid = _member_id(obj)
    if mid is None:
        return
    r = _redis_conn()
    if not r:
        return
    try:
        r.sadd(_room_key(room.id), str(mid))
        bump_room_generation(room)
    except Exception:
        logger.log_trace("room_scene_index: add failed")


def remove_from_room(room, obj) -> None:
    if not _enabled() or not room or not getattr(room, "id", None):
        return
    mid = _member_id(obj)
    if mid is None:
        return
    r = _redis_conn()
    if not r:
        return
    try:
        r.srem(_room_key(room.id), str(mid))
        bump_room_generation(room)
    except Exception:
        logger.log_trace("room_scene_index: remove failed")


def on_move(obj, origin, destination) -> None:
    if origin:
        remove_from_room(origin, obj)
    if destination:
        add_to_room(destination, obj)


def sync_room(room) -> None:
    """Rebuild Redis set from DB location contents."""
    if not _enabled() or not room or not getattr(room, "id", None):
        return
    r = _redis_conn()
    if not r:
        return
    try:
        from evennia.objects.models import ObjectDB

        paths = list(_typeclass_paths())
        ids = ObjectDB.objects.filter(
            db_location_id=int(room.id),
            db_typeclass_path__in=paths,
        ).values_list("id", flat=True)
        key = _room_key(room.id)
        pipe = r.pipeline()
        pipe.delete(key)
        members = [str(int(i)) for i in ids]
        if members:
            pipe.sadd(key, *members)
        pipe.execute()
        bump_room_generation(room)
    except Exception:
        logger.log_trace("room_scene_index: sync_room failed")


def rebuild_all() -> int:
    """Rebuild index for every room that has indexed occupants."""
    if not _enabled():
        return 0
    try:
        from evennia.objects.models import ObjectDB

        room_ids = (
            ObjectDB.objects.filter(
                db_typeclass_path__in=list(_typeclass_paths()),
                db_location__isnull=False,
            )
            .values_list("db_location_id", flat=True)
            .distinct()
        )
        from evennia.utils.search import search_object

        count = 0
        for rid in room_ids:
            if not rid:
                continue
            rooms = search_object("#%s" % rid)
            if rooms:
                sync_room(rooms[0])
                count += 1
        return count
    except Exception:
        logger.log_trace("room_scene_index: rebuild_all failed")
        return 0


def get_member_ids(room, *, exclude: Optional[Iterable] = None) -> Optional[Set[int]]:
    if not _enabled() or not room or not getattr(room, "id", None):
        return None
    r = _redis_conn()
    if not r:
        return None
    key = _room_key(room.id)
    try:
        if not r.exists(key):
            sync_room(room)
        raw = r.smembers(key)
        ids = set()
        for item in raw:
            if isinstance(item, bytes):
                item = item.decode("utf-8")
            try:
                ids.add(int(item))
            except (TypeError, ValueError):
                continue
        if exclude:
            ex_ids = set()
            for obj in exclude:
                if obj is None:
                    continue
                oid = getattr(obj, "id", None)
                if oid is not None:
                    ex_ids.add(int(oid))
            ids -= ex_ids
        return ids
    except Exception:
        logger.log_trace("room_scene_index: get_member_ids failed")
        return None


def resolve_recipients(room, *, exclude=None) -> Optional[List]:
    """
    Return live objects for scene members, or None to fall back to ``contents``.
    """
    ids = get_member_ids(room, exclude=exclude)
    if ids is None:
        return None
    if not ids:
        return []
    try:
        from evennia.objects.models import ObjectDB

        objs = list(ObjectDB.objects.filter(id__in=list(ids)))
        by_id = {int(o.id): o for o in objs}
        return [by_id[i] for i in sorted(ids) if i in by_id]
    except Exception:
        logger.log_trace("room_scene_index: resolve_recipients failed")
        return None
