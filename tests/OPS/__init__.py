"""Marks tests/OPS as a package so its conftest does not shadow the root one.

Without this file pytest imports every sibling conftest under the bare name
`conftest`, and the second one to load shadows the first.

`tests/` itself is deliberately NOT a package: the root conftest must stay the
one pytest finds by directory walk for BOTH categories, and the provisioning
fixtures live there because OPS builds on `silent_install` just as INS does.
"""
