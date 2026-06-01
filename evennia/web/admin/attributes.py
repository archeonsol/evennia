"""
Attribute admin.

Note that we don't present a separate admin for these, since they are only
relevant together with a specific object.

The Attribute Django model was removed in Phase 2 of the JSONB migration.
Attributes are now stored in db_attrs (JSONB) on the owning object's row.
"""
