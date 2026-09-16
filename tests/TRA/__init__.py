"""Marks tests/TRA as a package, for the same reason tests/INS and tests/OPS are.

Without it pytest imports every sibling conftest under the bare name `conftest`,
and the second one to load shadows the first. That matters here more than in a
category with no conftest: this one has a real `conftest.py`, so without this
file it and `tests/OPS/conftest.py` collide and one of the two silently wins.
"""
