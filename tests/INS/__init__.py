"""Marks tests/INS as a package, for the same reason tests/OPS is one.

Without it pytest imports every sibling conftest under the bare name
`conftest`, and the second one to load shadows the first.

`tests/` itself is deliberately NOT a package: the root conftest must stay the
one pytest finds by directory walk for every category.
"""
