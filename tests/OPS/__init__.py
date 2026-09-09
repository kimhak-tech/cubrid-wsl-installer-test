"""Marks tests/OPS as a package so its conftest does not shadow the root one.

Without this file pytest imports BOTH conftest modules under the bare name
`conftest`, and `tests/test_install_wizard_all_custom.py`'s
`from conftest import INS_004_WSL_NAME` then resolves to the wrong one, breaking
collection for the whole suite.

`tests/` itself is deliberately NOT a package -- making it one would rename every
test module and change what that import has to say.
"""
