"""Single source of truth for the application version.

``__version__`` is shown in the window title bar and logged at startup so a
running build can be told apart from others. For local/source runs it stays at
the dev default below; the CI build (``.github/workflows/build.yml``) rewrites
this line with a unique per-build string before packaging:

* manual test build      -> ``0.1.0-dev.<run_number>.g<short_sha>``
* tagged release (vX.Y.Z) -> ``X.Y.Z``

Bump the base ``0.1.0`` here when cutting a new minor/patch series; the CI reads
this base and appends the dev/build suffix for non-release builds.
"""

__version__ = "0.1.0-dev"
