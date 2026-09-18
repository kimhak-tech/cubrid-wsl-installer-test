"""Ways to drive the installer.

A driver EXERCISES the product; it never asserts. Reaching the Finish button is
not evidence that the install is correct -- the test case decides that, reading
the machine through `windows/` and `wsl/`. A driver that judges its own work is
a driver that tests itself.

Both drivers report the same result shape (`ok`, `timed_out`,
`duration_seconds`, `describe()`), so a case reads either the same way.
"""
