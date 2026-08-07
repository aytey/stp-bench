"""ANSI terminal formatting constants and time formatting."""

# ANSI escape codes
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
RST = "\033[0m"

# Terminal control
CL = "\033[2K"       # clear line
HIDE = "\033[?25l"   # hide cursor
SHOW = "\033[?25h"   # show cursor


def fmt_duration(secs):
    """Format seconds as a human-readable duration string."""
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = int(secs % 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"
