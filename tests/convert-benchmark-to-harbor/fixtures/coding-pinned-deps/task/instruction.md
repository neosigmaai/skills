Edit `/app/solution.py` so that `is_legacy(version)` returns `True` when the
installed `packaging` library parses `version` as a legacy (non-PEP 440)
version, and `False` when it parses it as a PEP 440 version.
