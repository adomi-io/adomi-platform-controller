"""Console entry point: serve the API with uvicorn."""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    uvicorn.run(
        "adomi_platform_api.app:app",
        host=os.environ.get("ADOMI_API_HOST", "0.0.0.0"),  # noqa: S104 - in-cluster service
        port=int(os.environ.get("ADOMI_API_PORT", "8080")),
    )


if __name__ == "__main__":
    main()
