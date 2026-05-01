"""reCAPTCHA v2 solver for Playwright."""

_async_import_error = None
_sync_import_error = None

try:
    from .async_solver import AsyncSolver
except Exception as e:
    AsyncSolver = None
    _async_import_error = e

try:
    from .sync_solver import SyncSolver
except Exception as e:
    SyncSolver = None
    _sync_import_error = e

__all__ = ["AsyncSolver", "SyncSolver", "solve"]


async def solve(page) -> bool:
    print("\n  [ReCaptchaV2] Starte reCAPTCHA v2 Audio-Solver...")

    if AsyncSolver is None:
        detail = _async_import_error or _sync_import_error
        print(f"  [ReCaptchaV2] Solver nicht verfuegbar (Dependency fehlt): {detail}")
        print("  [ReCaptchaV2] Bitte fehlende Pakete installieren (z.B. tenacity).")
        return False

    try:
        solver = AsyncSolver(page)
        token = await solver.solve_recaptcha(attempts=3)
        if token:
            print("  [ReCaptchaV2] reCAPTCHA geloest.")
            return True
        print("  [ReCaptchaV2] Konnte Token nicht generieren.")
        return False
    except Exception as e:
        print(f"  [ReCaptchaV2] Fehler beim Loesen: {e}")
        return False
