import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tapwire import tools
from tapwire.tools import ToolNotFound, find_tool


def make_exe(directory: Path, name="ffmpeg", mode=0o755) -> Path:
    path = directory / name
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(mode)
    return path


class ToolLookupTests(unittest.TestCase):
    def setUp(self):
        find_tool.cache_clear()
        self.addCleanup(find_tool.cache_clear)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._old_cwd = os.getcwd()
        self.addCleanup(os.chdir, self._old_cwd)

    def mkdir(self, name, mode=0o755) -> Path:
        d = self.root / name
        d.mkdir()
        d.chmod(mode)
        return d

    def env(self, path, **extra):
        return mock.patch.dict(os.environ, {"PATH": path, **extra}, clear=False)

    def as_root_owner(self):
        """Pretend files owned by the current user are root-owned (tests don't run as root)."""
        return mock.patch.object(tools, "ROOT_UID", os.getuid())

    # --- the attack in question: a binary named ffmpeg next to the script / in the cwd ---
    def test_cwd_and_relative_path_entries_are_never_searched(self):
        here = self.mkdir("here")
        make_exe(here)
        os.chdir(here)
        for path in ("", ".", ":", "::", "here", "./here", ":/nonexistent", "/nonexistent:"):
            with self.subTest(PATH=path), self.env(path), self.as_root_owner():
                with self.assertRaises(ToolNotFound):
                    find_tool("ffmpeg")
                find_tool.cache_clear()

    def test_user_owned_directory_in_path_is_ignored(self):
        if os.getuid() == 0:
            self.skipTest("running as root")
        d = self.mkdir("userbin")
        make_exe(d)
        with self.env(str(d)):  # real ROOT_UID: this dir is owned by us, not root
            with self.assertRaises(ToolNotFound):
                find_tool("ffmpeg")

    # --- the rules, with ownership faked ---
    def test_trusted_directory_is_used(self):
        d = self.mkdir("sysbin")
        exe = make_exe(d)
        with self.env(str(d)), self.as_root_owner():
            self.assertEqual(find_tool("ffmpeg"), str(exe))

    def test_untrusted_directory_before_trusted_one_loses(self):
        evil = self.mkdir("evil", 0o777)
        good = self.mkdir("good")
        make_exe(evil)
        good_exe = make_exe(good)
        with self.env(f"{evil}:{good}"), self.as_root_owner():
            self.assertEqual(find_tool("ffmpeg"), str(good_exe))

    def test_writable_by_group_or_others_directory_refused(self):
        for mode in (0o777, 0o775, 0o757):
            with self.subTest(mode=oct(mode)):
                d = self.mkdir(f"d{mode:o}", mode)
                make_exe(d)
                with self.env(str(d)), self.as_root_owner():
                    with self.assertRaises(ToolNotFound):
                        find_tool("ffmpeg")
                find_tool.cache_clear()

    def test_writable_or_non_executable_file_refused(self):
        for mode in (0o777, 0o775, 0o757, 0o644):
            with self.subTest(mode=oct(mode)):
                d = self.mkdir(f"f{mode:o}")
                make_exe(d, mode=mode)
                with self.env(str(d)), self.as_root_owner():
                    with self.assertRaises(ToolNotFound):
                        find_tool("ffmpeg")
                find_tool.cache_clear()

    def test_symlink_to_untrusted_file_refused(self):
        trusted = self.mkdir("trusted")
        elsewhere = self.mkdir("elsewhere", 0o777)
        target = make_exe(elsewhere, "real", 0o777)
        (trusted / "ffmpeg").symlink_to(target)  # trusted dir, but the real file is world-writable
        with self.env(str(trusted)), self.as_root_owner():
            with self.assertRaises(ToolNotFound):
                find_tool("ffmpeg")

    # --- explicit override ---
    def test_override_must_be_absolute_executable(self):
        d = self.mkdir("opt")
        exe = make_exe(d, "my-ffmpeg")
        with self.env("", TAPWIRE_FFMPEG=str(exe)):
            self.assertEqual(find_tool("ffmpeg"), str(exe))
        for bad in ("ffmpeg", "./my-ffmpeg", str(d / "missing"), str(d)):
            find_tool.cache_clear()
            with self.subTest(override=bad), self.env("", TAPWIRE_FFMPEG=bad):
                with self.assertRaises(ToolNotFound):
                    find_tool("ffmpeg")

    def test_dash_in_name_maps_to_underscore_in_env_var(self):
        d = self.mkdir("opt2")
        exe = make_exe(d, "pw-record")
        with self.env("", TAPWIRE_PW_RECORD=str(exe)):
            self.assertEqual(find_tool("pw-record"), str(exe))

    # --- on the real machine ---
    def test_real_system_tools_resolve_to_system_paths(self):
        for name in ("ffmpeg", "pw-record", "pw-link", "pw-dump"):
            find_tool.cache_clear()
            try:
                path = find_tool(name)
            except ToolNotFound:
                self.skipTest(f"{name} not installed in a trusted directory")
            self.assertTrue(os.path.isabs(path))
            st = os.stat(os.path.realpath(path))
            self.assertEqual(st.st_uid, 0)
            self.assertFalse(st.st_mode & (stat.S_IWGRP | stat.S_IWOTH))


if __name__ == "__main__":
    unittest.main()
