"""Entry point for the single-file Windows program (built by scripts/make_exe.py). Same as: python -m core"""
import multiprocessing

from core.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
