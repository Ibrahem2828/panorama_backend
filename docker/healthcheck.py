import http.client
import os
import sys


def main() -> int:
    port = int(os.getenv("PORT", "8000"))
    path = os.getenv("HEALTHCHECK_PATH", "/api/v1/health/")

    connection = None

    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            port,
            timeout=5,
        )

        connection.request(
            "GET",
            path,
            headers={
                "Host": "127.0.0.1",
            },
        )

        response = connection.getresponse()

        if 200 <= response.status < 400:
            return 0

        return 1

    except Exception:
        return 1

    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
