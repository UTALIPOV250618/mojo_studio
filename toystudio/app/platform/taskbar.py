"""Progress on the Windows taskbar button (ITaskbarList3); a silent no-op everywhere else."""

from __future__ import annotations

import logging
import sys
from typing import Any

from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)

TBPF_NOPROGRESS = 0
TBPF_NORMAL = 2
_CLSID_TASKBAR_LIST = "{56FDF344-FD6D-11d0-958A-006097C9A090}"
_SCALE = 1000


def _create_taskbar_list() -> Any:
    """Create the COM object with a minimal ITaskbarList3 vtable (Windows only)."""
    from ctypes import HRESULT
    from ctypes import c_ulonglong as ulonglong
    from ctypes.wintypes import BOOL, DWORD, HWND

    import comtypes
    from comtypes import COMMETHOD, GUID, IUnknown

    class ITaskbarList(IUnknown):  # type: ignore[misc]
        _iid_ = GUID("{56FDF342-FD6D-11d0-958A-006097C9A090}")
        _methods_ = [  # noqa: RUF012
            COMMETHOD([], HRESULT, "HrInit"),
            COMMETHOD([], HRESULT, "AddTab", (["in"], HWND, "hwnd")),
            COMMETHOD([], HRESULT, "DeleteTab", (["in"], HWND, "hwnd")),
            COMMETHOD([], HRESULT, "ActivateTab", (["in"], HWND, "hwnd")),
            COMMETHOD([], HRESULT, "SetActiveAlt", (["in"], HWND, "hwnd")),
        ]

    class ITaskbarList2(ITaskbarList):  # type: ignore[misc, valid-type]
        _iid_ = GUID("{602D4995-B13A-429B-A66E-1935E44F4317}")
        _methods_ = [  # noqa: RUF012
            COMMETHOD(
                [],
                HRESULT,
                "MarkFullscreenWindow",
                (["in"], HWND, "hwnd"),
                (["in"], BOOL, "fFullscreen"),
            ),
        ]

    class ITaskbarList3(ITaskbarList2):  # type: ignore[misc, valid-type]
        _iid_ = GUID("{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}")
        _methods_ = [  # noqa: RUF012
            COMMETHOD(
                [],
                HRESULT,
                "SetProgressValue",
                (["in"], HWND, "hwnd"),
                (["in"], ulonglong, "ullCompleted"),
                (["in"], ulonglong, "ullTotal"),
            ),
            COMMETHOD(
                [],
                HRESULT,
                "SetProgressState",
                (["in"], HWND, "hwnd"),
                (["in"], DWORD, "tbpFlags"),
            ),
        ]

    obj = comtypes.CoCreateInstance(
        GUID(_CLSID_TASKBAR_LIST), interface=ITaskbarList3, clsctx=comtypes.CLSCTX_INPROC_SERVER
    )
    obj.HrInit()
    return obj


class TaskbarProgress:
    def __init__(self, window: QWidget) -> None:
        self._window = window
        self._com: Any = None
        self._broken = sys.platform != "win32"

    def set_progress(self, percent: float | None) -> None:
        """None clears the progress; 0..100 fills the taskbar button."""
        if self._broken:
            return
        try:
            if self._com is None:
                self._com = _create_taskbar_list()
            hwnd = int(self._window.window().winId())
            if percent is None:
                self._com.SetProgressState(hwnd, TBPF_NOPROGRESS)
                return
            value = round(min(max(percent, 0.0), 100.0) * _SCALE / 100)
            self._com.SetProgressState(hwnd, TBPF_NORMAL)
            self._com.SetProgressValue(hwnd, value, _SCALE)
        except Exception:
            log.debug("taskbar progress disabled", exc_info=True)
            self._broken = True
