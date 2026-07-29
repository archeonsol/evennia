"""Native bounded paging for storage-script listings."""

from django.core.paginator import Paginator

from evennia.utils.evmore import EvMore
from evennia.utils.evtable import EvTable
from evennia.utils.utils import crop

__all__ = ["ScriptEvMore"]


class ScriptEvMore(EvMore):
    """Build only the visible storage-script table page."""

    def init_pages(self, scripts):
        """Paginate before rendering to bound memory use."""
        script_pages = Paginator(scripts, max(1, int(self.height / 2)))
        super().init_pages(script_pages)

    def page_formatter(self, scripts):
        """Render one page of storage scripts as a table."""
        if not scripts:
            return "<No scripts>"
        table = EvTable(
            "|wdbref|n",
            "|wobj|n",
            "|wkey|n",
            "|wtypeclass|n",
            "|wdesc|n",
            align="r",
            border="tablecols",
            width=self.width,
        )
        for script in scripts:
            table.add_row(
                f"#{script.id}",
                (
                    f"{script.obj.key}({script.obj.dbref})"
                    if getattr(script, "obj", None)
                    else "<Global>"
                ),
                script.db_key,
                script.typeclass_path.rsplit(".", 1)[-1],
                crop(script.desc, width=20),
            )
        return str(table)
