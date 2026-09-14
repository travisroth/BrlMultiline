# BrlMultiline: a device write that gives up.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Write to an overlapped handle, and stop waiting if the device never takes it.

`hwIo.IoBase.write` starts an overlapped write and then waits for it with
`GetOverlappedResult(..., bWait=True)`, which is to say forever. A device that stops taking
output reports never completes that write, and whatever thread made it never returns. On the
morning of 2026-09-14 that was a Monarch: its first pin report never finished, the handler's
I/O thread sat in `GetOverlappedResult` holding the driver's write lock, and the main thread
queued up behind the lock in a repaint. NVDA started and was frozen before it said a word.

This is the same write with a deadline. If the device has not taken the report in time the
write is cancelled and `WriteTimedOut` is raised, which the driver treats like any other
failed write: the device is lost and recovery begins.

Imports nothing from NVDA, so it can be tested against a real Windows handle without NVDA
standing behind it. The kernel32 functions are bound on a private `WinDLL`, so the argument
types declared here cannot disturb anyone else's use of `ctypes.windll.kernel32`.
"""

import ctypes
from ctypes import wintypes

ERROR_IO_PENDING = 997
WAIT_OBJECT_0 = 0

CANCEL_GRACE = 1.0
"""Seconds to wait for a cancelled write to actually finish.

A driver honours `CancelIoEx` promptly, so this is normally over at once. One that does not
is not waited for either.
"""


class WriteTimedOut(TimeoutError):
	"""The device did not take a write within the time allowed."""


class _OVERLAPPED(ctypes.Structure):
	_fields_ = (
		("Internal", ctypes.c_void_p),
		("InternalHigh", ctypes.c_void_p),
		("Offset", wintypes.DWORD),
		("OffsetHigh", wintypes.DWORD),
		("hEvent", wintypes.HANDLE),
	)


_abandoned: list = []
"""Writes the kernel never let go of, kept alive on purpose.

A write that ignored its cancellation still owns its OVERLAPPED structure and its buffer, and
will write into both when it does complete. Freeing them would turn a hung device into memory
corruption, so they are parked here instead. Each one costs a report's worth of bytes and
an event handle, and there is at most one per failed write.
"""

_kernel32 = None


def _api():
	""":return: kernel32, with the prototypes this module needs, bound once."""
	global _kernel32
	if _kernel32 is None:
		kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
		overlapped = ctypes.POINTER(_OVERLAPPED)
		kernel32.CreateEventW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
		kernel32.CreateEventW.restype = wintypes.HANDLE
		kernel32.WriteFile.argtypes = (
			wintypes.HANDLE,
			ctypes.c_void_p,
			wintypes.DWORD,
			ctypes.POINTER(wintypes.DWORD),
			overlapped,
		)
		kernel32.WriteFile.restype = wintypes.BOOL
		kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
		kernel32.WaitForSingleObject.restype = wintypes.DWORD
		kernel32.CancelIoEx.argtypes = (wintypes.HANDLE, overlapped)
		kernel32.CancelIoEx.restype = wintypes.BOOL
		kernel32.GetOverlappedResult.argtypes = (
			wintypes.HANDLE,
			overlapped,
			ctypes.POINTER(wintypes.DWORD),
			wintypes.BOOL,
		)
		kernel32.GetOverlappedResult.restype = wintypes.BOOL
		kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
		kernel32.CloseHandle.restype = wintypes.BOOL
		_kernel32 = kernel32
	return _kernel32


def _milliseconds(seconds: float) -> int:
	return max(0, int(seconds * 1000))


def writeWithin(handle, buffer, size: int, timeout: float) -> None:
	"""Write a buffer to an overlapped handle, giving up after `timeout` seconds.

	:param handle: a handle opened with `FILE_FLAG_OVERLAPPED`.
	:param buffer: a ctypes buffer at least `size` bytes long. It must stay alive until this
		returns, and is kept alive beyond that if the kernel does not release the write.
	:param size: how many bytes of it to write.
	:param timeout: seconds the device has to take the write.
	:raises WriteTimedOut: if it did not, in which case the write has been cancelled.
	:raises OSError: if the write failed outright.
	"""
	kernel32 = _api()
	event = kernel32.CreateEventW(None, True, False, None)
	if not event:
		raise ctypes.WinError(ctypes.get_last_error())
	overlapped = _OVERLAPPED()
	overlapped.hEvent = event
	written = wintypes.DWORD()
	if not kernel32.WriteFile(handle, buffer, size, None, ctypes.byref(overlapped)):
		error = ctypes.get_last_error()
		if error != ERROR_IO_PENDING:
			kernel32.CloseHandle(event)
			raise ctypes.WinError(error)
		if kernel32.WaitForSingleObject(event, _milliseconds(timeout)) != WAIT_OBJECT_0:
			kernel32.CancelIoEx(handle, ctypes.byref(overlapped))
			if kernel32.WaitForSingleObject(event, _milliseconds(CANCEL_GRACE)) != WAIT_OBJECT_0:
				_abandoned.append((overlapped, buffer, event))
				raise WriteTimedOut(f"the device did not take a write in {timeout} seconds, nor let it go")
			# Finished while being cancelled. It may have completed rather than been aborted,
			# and a write that got there is a write that got there.
			completed = kernel32.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(written), False)
			kernel32.CloseHandle(event)
			if completed:
				return
			raise WriteTimedOut(f"the device did not take a write in {timeout} seconds")
	completed = kernel32.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(written), False)
	error = ctypes.get_last_error()
	kernel32.CloseHandle(event)
	if not completed:
		raise ctypes.WinError(error)
