"""Ways to drive the installer.

A driver EXERCISES the product; it never asserts. Reaching the Finish button is
not evidence that the install is correct -- state.py and verify.py decide that,
identically for both drivers. A driver that judges its own work is a driver that
tests itself.

Both drivers report the same result shape (`ok`, `timed_out`,
`duration_seconds`, `describe()`), so the install fixture can treat them as
interchangeable. That interchangeability is the architectural claim INS-001
exists to test.
"""
