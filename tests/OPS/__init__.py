"""Marks tests/OPS as a package so its conftest does not shadow the root one.

Without this file pytest imports every sibling conftest under the bare name
`conftest`, and the second one to load shadows the first.

`tests/` itself is deliberately NOT a package: the root conftest must stay the
one pytest finds by directory walk for every category, because the shared
fixtures -- `settings`, `installer`, `run_dir`, `note` -- live there.
"""
