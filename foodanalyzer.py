"""Entry point shim so `python -m foodanalyzer ...` works, as the brief's
minimum runnable demo expects (§5.2.2: "python -m foodanalyzer analyze
<path>"). The actual implementation lives in src/cli.py.
"""

from src.cli import main

if __name__ == "__main__":
    main()
