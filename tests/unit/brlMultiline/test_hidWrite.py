# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""The Monarch's bounded write, against a real Windows handle.

The driver tests cannot show that the write actually gives up: their device is a Python object.
This file uses a named pipe whose other end does not read, which is the same situation a
hung device is in from the writer's side — an overlapped `WriteFile` that stays pending — and
checks that `writeWithin` returns control, cancels, and raises.
"""

import ctypes
import importlib.util
import itertools
import os
import sys
import threading
import time
import unittest
from ctypes import wintypes

MODULE_PATH = os.path.abspath(
	os.path.join(
		os.path.dirname(__file__),
		"..",
		"..",
		"..",
		"addon",
		"brailleDisplayDrivers",
		"brlMultilineMonarch",
		"hidWrite.py",
	),
)


def _loadHidWrite():
	"""Import `hidWrite` on its own. It imports nothing from NVDA, so it needs no stubs."""
	spec = importlib.util.spec_from_file_location("brlMultilineMonarch_hidWrite_standalone", MODULE_PATH)
	assert spec is not None and spec.loader is not None
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


PIPE_ACCESS_OUTBOUND = 0x2
FILE_FLAG_OVERLAPPED = 0x40000000
GENERIC_READ = 0x80000000
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
PAYLOAD = 1 << 20
"""Far more than a pipe will buffer, so the write stays pending until someone reads."""

_names = itertools.count()


@unittest.skipUnless(sys.platform == "win32", "overlapped handles are a Windows thing")
class TestWriteWithin(unittest.TestCase):
	def setUp(self):
		self.hidWrite = _loadHidWrite()
		kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
		kernel32.CreateNamedPipeW.argtypes = (
			wintypes.LPCWSTR,
			wintypes.DWORD,
			wintypes.DWORD,
			wintypes.DWORD,
			wintypes.DWORD,
			wintypes.DWORD,
			wintypes.DWORD,
			ctypes.c_void_p,
		)
		kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
		kernel32.CreateFileW.argtypes = (
			wintypes.LPCWSTR,
			wintypes.DWORD,
			wintypes.DWORD,
			ctypes.c_void_p,
			wintypes.DWORD,
			wintypes.DWORD,
			wintypes.HANDLE,
		)
		kernel32.CreateFileW.restype = wintypes.HANDLE
		kernel32.ReadFile.argtypes = (
			wintypes.HANDLE,
			ctypes.c_void_p,
			wintypes.DWORD,
			ctypes.POINTER(wintypes.DWORD),
			ctypes.c_void_p,
		)
		kernel32.ReadFile.restype = wintypes.BOOL
		kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
		kernel32.CloseHandle.restype = wintypes.BOOL
		self.kernel32 = kernel32
		name = f"\\\\.\\pipe\\brlMultilineHidWrite-{os.getpid()}-{next(_names)}"
		self.writer = kernel32.CreateNamedPipeW(
			name,
			PIPE_ACCESS_OUTBOUND | FILE_FLAG_OVERLAPPED,
			0,
			1,
			1,
			1,
			0,
			None,
		)
		self.assertNotEqual(INVALID_HANDLE_VALUE, self.writer, ctypes.WinError(ctypes.get_last_error()))
		self.reader = kernel32.CreateFileW(name, GENERIC_READ, 0, None, OPEN_EXISTING, 0, None)
		self.assertNotEqual(INVALID_HANDLE_VALUE, self.reader, ctypes.WinError(ctypes.get_last_error()))
		self.buffer = ctypes.create_string_buffer(PAYLOAD)

	def tearDown(self):
		for handle in (self.reader, self.writer):
			if handle and handle != INVALID_HANDLE_VALUE:
				self.kernel32.CloseHandle(handle)

	def _drain(self) -> None:
		"""Read everything the writer sends, as a device taking its reports would."""
		chunk = ctypes.create_string_buffer(65536)
		got = wintypes.DWORD()
		total = 0
		while total < PAYLOAD:
			if not self.kernel32.ReadFile(self.reader, chunk, len(chunk), ctypes.byref(got), None):
				return
			total += got.value

	def test_givesUpOnAWriteNobodyTakes(self):
		started = time.monotonic()
		with self.assertRaises(self.hidWrite.WriteTimedOut):
			self.hidWrite.writeWithin(self.writer, self.buffer, PAYLOAD, 0.2)
		elapsed = time.monotonic() - started
		self.assertGreaterEqual(elapsed, 0.15)
		self.assertLess(elapsed, 0.2 + self.hidWrite.CANCEL_GRACE + 1.0)

	def test_aTimedOutWriteIsCancelledRatherThanLeftPending(self):
		"""A write the kernel let go of is not parked as abandoned."""
		with self.assertRaises(self.hidWrite.WriteTimedOut):
			self.hidWrite.writeWithin(self.writer, self.buffer, PAYLOAD, 0.1)
		self.assertEqual([], self.hidWrite._abandoned)

	def test_returnsWhenTheWriteIsTaken(self):
		reader = threading.Thread(target=self._drain, daemon=True)
		reader.start()
		self.hidWrite.writeWithin(self.writer, self.buffer, PAYLOAD, 10.0)
		reader.join(5)

	def test_aWriteThatFailsOutrightIsNotATimeout(self):
		"""A closed far end is a lost device too, but not a hung one, and must say so at once."""
		self.kernel32.CloseHandle(self.reader)
		self.reader = None
		started = time.monotonic()
		with self.assertRaises(OSError) as raised:
			self.hidWrite.writeWithin(self.writer, self.buffer, PAYLOAD, 5.0)
		self.assertNotIsInstance(raised.exception, self.hidWrite.WriteTimedOut)
		self.assertLess(time.monotonic() - started, 1.0)


if __name__ == "__main__":
	unittest.main()
