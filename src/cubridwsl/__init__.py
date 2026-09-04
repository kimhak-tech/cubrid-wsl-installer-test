"""QA automation framework for the CUBRID For WSL Installer.

Layering, and it is worth keeping:

    drivers/   drive the installer (silent CLI, wizard UI). They never assert.
    state      read what is actually true on the machine. It never interprets.
    verify     decide whether that state is what the options imply.
    reset      bring the machine back to a known clean starting point.

A driver that judges its own work is a driver that tests itself, so both
drivers assert through `verify` and no other route. That is what makes "the
wizard reaches the same state as a silent install" a tested fact.
"""

__version__ = "0.1.0"
