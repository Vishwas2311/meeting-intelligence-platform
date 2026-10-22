"""Exercise the API-only Linux gateway using isolated synthetic runtime services."""

from smoke_web import run

if __name__ == "__main__":
    run(api_only=True)
