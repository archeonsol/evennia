"""
How much memory this process is using right now.

Two parts of the engine need the answer: the idmapper (is the cache worth sweeping)
and the garbage collector (has the process grown since it last cleaned up). Both
want the *current* resident set, not the peak: ``ru_maxrss`` never falls, so a check
built on it keeps reporting pressure for as long as the process lives once memory
has ever spiked.
"""

from __future__ import annotations

import os

from evennia.utils import logger


def rss_from_statm(path="/proc/self/statm", page_size=None):
    """Read the resident set size from a Linux ``statm`` file, in MB.

    Args:
        path (str, optional): The ``statm`` file to read.
        page_size (int, optional): Bytes per page. Defaults to the system page size.

    Returns:
        float or None: Resident megabytes, or ``None`` if the file cannot be read.
    """
    try:
        with open(path, "rb") as handle:
            resident_pages = int(handle.read().split()[1])
        page_size = page_size or os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError, AttributeError):
        return None
    return resident_pages * page_size / (1024.0 * 1024.0)


def current_rss_mb():
    """Return the resident memory of this process right now, in MB.

    The current resident set is read from psutil, then ``/proc/self/statm``. The peak
    is only a last resort, for hosts that offer nothing better.

    Returns:
        float or None: Resident megabytes, or ``None`` if the host cannot say.
    """
    try:
        import psutil

        return psutil.Process(os.getpid()).memory_info().rss / (1024.0 * 1024.0)
    except ImportError:
        pass
    except Exception:
        logger.log_trace("process memory: psutil could not read this process's memory")
    rss = rss_from_statm()
    if rss is not None:
        return rss
    try:
        import resource
        import sys

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (ImportError, OSError, ValueError):
        return None
    # ru_maxrss is bytes on macOS and kilobytes elsewhere
    return peak / (1024.0 * 1024.0) if sys.platform == "darwin" else peak / 1024.0
