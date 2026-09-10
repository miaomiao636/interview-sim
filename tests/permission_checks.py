"""POSIX modes and Windows usability are different contracts, not equivalent ACLs."""
import os


def assert_storage_permissions(test, path, mode):
    test.assertTrue(path.exists())
    if os.name == 'nt':
        # chmod does not establish a private DACL on Windows. Privacy there relies
        # on the user's profile ACL, documented in SECURITY.md; do not fake 0600.
        test.assertTrue(os.access(path, os.R_OK | os.W_OK))
    else:
        test.assertEqual(path.stat().st_mode & 0o777, mode)
