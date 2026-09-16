"""Marks tests/LCM as a package, for the same reason tests/INS and tests/OPS are.

Without it pytest imports every sibling conftest under the bare name `conftest`,
and the second one to load shadows the first. There is no conftest here yet;
the file exists so adding one later is not a trap.
"""
