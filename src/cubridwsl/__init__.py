"""QA automation framework for the CUBRID For WSL Installer.

Layering, and it is worth keeping:

    drivers/   drive the installer (silent CLI, wizard UI). They never assert.
    windows/   what the product left on Windows, one module per surface.
    wsl/       what the product left inside WSL, and CUBRID running there.

A driver that judges its own work is a driver that tests itself, so the test
case does the asserting, reading the machine through `windows/` and `wsl/`.
"""

__version__ = "0.1.0"
