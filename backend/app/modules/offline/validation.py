"""Validate untrusted uploads in disposable, deadline/memory-bounded processes."""
import math
import multiprocessing
import os

from app.shared.errors import AppError


_windows_job = None


def _limit_memory(max_bytes):
    # A hard process-memory limit is applied before invoking native parsers.
    # Keep the Windows job handle alive for the entire disposable child.
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class Io(ctypes.Structure):
            _fields_ = [(key, ctypes.c_ulonglong) for key in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", Io),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        handle = kernel.CreateJobObjectW(None, None)
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
        info.ProcessMemoryLimit = max_bytes
        if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise RuntimeError("validation_limit_unavailable")
        if not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
            raise RuntimeError("validation_limit_unavailable")
        global _windows_job
        _windows_job = handle
    else:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (max_bytes, max_bytes))


def _child(connection, kind, data, mime, max_pixels, max_pages):
    try:
        _limit_memory(2 * 1024**3 if kind == "image" else 512 * 1024**2)
        if kind == "image":
            from app.modules.files.images import InvalidImage, process_image
            try:
                image = process_image(data, mime, max_pixels)
            except InvalidImage as error:
                connection.send(("invalid", str(error)))
                return
            connection.send(("ok", {"sha256": image.sha256}))
        else:
            import pypdfium2 as pdfium
            import pypdfium2.raw as raw
            try:
                with pdfium.PdfDocument(data) as document:
                    count = len(document)
                    if not 1 <= count <= max_pages:
                        connection.send(("invalid", "pdf_page_limit"))
                        return
                    for index in range(count):
                        page = document[index]
                        try:
                            width, height = page.get_size()
                            if (not all(math.isfinite(value) and 0 < value <= 14400 for value in (width, height))
                                    or max(width, height) / min(width, height) > 20):
                                connection.send(("invalid", "pdf_page_dimensions"))
                                return
                            scale = min(200 / 72, 3200 / max(width, height))
                            if math.ceil(width * scale) * math.ceil(height * scale) > max_pixels:
                                connection.send(("invalid", "pdf_page_pixels"))
                                return
                        finally:
                            page.close()
                    connection.send(("ok", {"page_count": count}))
            except pdfium.PdfiumError as error:
                connection.send(("invalid", "pdf_password_required" if error.err_code == raw.FPDF_ERR_PASSWORD else "pdf_invalid"))
    except (MemoryError, RuntimeError, OSError):
        connection.send(("unavailable", "validation_resource_limit"))
    except Exception:
        # No parser exception text, uploaded data or paths cross the API boundary.
        connection.send(("invalid", "image_invalid" if kind == "image" else "pdf_invalid"))
    finally:
        connection.close()


def validate_upload(kind, data, *, mime="application/pdf", max_pixels, max_pages=1, timeout=30):
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_child, args=(child, kind, data, mime, max_pixels, max_pages), daemon=True)
    try:
        process.start()
        child.close()
        if not parent.poll(timeout):
            raise AppError(503, "validation_timeout", "文件校验超时，请稍后重试")
        try:
            status, result = parent.recv()
        except EOFError:
            raise AppError(503, "validation_unavailable", "文件校验暂不可用，请稍后重试") from None
        if status == "invalid":
            raise AppError(422, result, "文件未通过内容校验，请重新选择文件")
        if status != "ok":
            raise AppError(503, "validation_unavailable", "文件校验暂不可用，请稍后重试")
        return result
    finally:
        parent.close()
        child.close()
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=3)
            if process.is_alive():
                process.kill()
                process.join(timeout=3)
            process.close()
