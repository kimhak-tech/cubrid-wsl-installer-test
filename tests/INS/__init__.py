"""Marks tests/INS as a package, for the same reason tests/OPS is one.

Without it pytest imports every sibling conftest under the bare name `conftest`,
and the second one to load shadows the first. The package name also keeps the
three module names here distinct from anything a future category adds.

`tests/` itself is deliberately NOT a package: the root conftest must stay the
one pytest finds by directory walk for BOTH categories, and the provisioning
fixtures live there because OPS builds on `silent_install` just as INS does.
"""
