from packaging.version import LegacyVersion, parse


def is_legacy(version):
    return isinstance(parse(version), LegacyVersion)
